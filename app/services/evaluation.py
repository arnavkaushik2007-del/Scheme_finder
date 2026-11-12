"""Evaluation dashboard (spec section 6). Every metric is computed from labelled data, never assumed.

Labelled sets live in app/data/eval/ (synthetic, hand-labelled):
  eligibility_cases.json  - profiles with hand-checked outcomes        -> eligibility accuracy
  rule_guidelines.json    - guideline excerpts with expected rules     -> rule extraction P/R/F1
  rag_questions.json      - questions with expected scheme/section     -> RAG correctness, citation
                            and abstention quality                        accuracy, abstention quality
  scam_eval.json          - held-out messages (not used for training)  -> scam precision / recall
  sample documents        - synthetic PDFs with known field values     -> document extraction accuracy
  speech/*.wav + *.txt    - optional recordings                        -> Hindi word error rate
Results are stored in eval_runs with the versions of every model and prompt, so runs can be compared.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.ai import assistant, guard, rule_extraction
from app.ai import extraction as doc_extraction
from app.ai.embeddings import get_embedder
from app.ai.llm import get_llm
from app.services import eligibility, interview, scam, schemes

EVAL_DIR = schemes.DATA_DIR / "eval"

EXPECTED_DOC_FIELDS = {
    "aadhaar_sunita.pdf": ("aadhaar", {"full_name": "Sunita Devi", "date_of_birth": "1958-03-12", "gender": "female",
                                       "aadhaar_last4": "8901", "state": "Bihar"}),
    "income_certificate_sunita.pdf": ("income_certificate", {"full_name": "Sunita Devi", "annual_income": 60000,
                                                             "state": "Bihar", "district": "Gaya"}),
    "ration_card_sunita.pdf": ("ration_card", {"full_name": "Sunita Devi", "is_bpl": True, "family_size": 3}),
    "caste_certificate_ramesh.pdf": ("caste_certificate", {"full_name": "Ramesh Kumar", "category": "sc",
                                                           "state": "Uttar Pradesh"}),
    "land_record_ramesh.pdf": ("land_record", {"full_name": "Ramesh Kumar", "land_holding_hectares": 1.2}),
    "bank_passbook_ramesh.pdf": ("bank_passbook", {"full_name": "Ramesh Kumar", "has_bank_account": True,
                                                   "bank_account_last4": "7890"}),
    "marksheet_priya.pdf": ("marksheet", {"full_name": "Priya Sharma", "marks_percent": 90.4,
                                          "education_level": "higher_secondary"}),
    "disability_certificate_ramesh.pdf": ("disability_certificate", {"full_name": "Ramesh Kumar", "disability_percent": 45}),
}


def _load(name: str) -> list[dict[str, Any]]:
    p = EVAL_DIR / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def versions() -> dict[str, Any]:
    llm = get_llm()
    return {
        "rules_engine": "rules-v1", "embedder": get_embedder().name, "llm": llm.name,
        "llm_model": getattr(llm, "model", "none"), "scam_model": scam.MODEL_VERSION,
        "prompts": {"assistant_answer": _h(assistant.GEN_SYSTEM), "guard_entailment": _h(guard.NLI_SYSTEM),
                    "document_extraction": _h(doc_extraction.LLM_SYSTEM), "rule_extraction": _h(rule_extraction.LLM_SYSTEM)},
    }


async def eval_eligibility(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    cases = _load("eligibility_cases.json")
    rules = {s["slug"]: s["rules"] async for s in db.schemes.find({}, {"slug": 1, "rules": 1})}
    seed = {r["slug"]: r["rules"] for r in schemes.load_seed_files()[0]}
    wrong, times = [], []
    for c in cases:
        r = seed.get(c["scheme"]) or rules.get(c["scheme"])  # labelled against the seed rules
        t0 = time.perf_counter()
        got = eligibility.evaluate(r, c["profile"]).status
        times.append((time.perf_counter() - t0) * 1000)
        if got != c["expected"]:
            wrong.append({"id": c["id"], "scheme": c["scheme"], "expected": c["expected"], "got": got})
    n = len(cases)
    return {"n": n, "accuracy": round((n - len(wrong)) / n, 4) if n else None, "errors": wrong[:20],
            "median_ms_per_check": round(statistics.median(times), 3) if times else None}


def eval_rule_extraction() -> dict[str, Any]:
    items = _load("rule_guidelines.json")
    tp = fp = fn = 0
    details = []

    def key(r: dict[str, Any]) -> tuple[str, str, str]:
        return (r["field"], r["op"], json.dumps(r.get("value"), sort_keys=True))

    for g in items:
        got, _ = rule_extraction.pattern_rules(g["text"])
        gs, es = {key(r) for r in got}, {key(r) for r in g["expected"]}
        tp += len(gs & es)
        fp += len(gs - es)
        fn += len(es - gs)
        if gs != es:
            details.append({"id": g["id"], "missing": sorted(es - gs), "extra": sorted(gs - es)})
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else None
    return {"n": len(items), "method": "pattern" if get_llm().name == "mock" else "pattern (LLM path not evaluated)",
            "precision": None if p is None else round(p, 3), "recall": None if r is None else round(r, 3),
            "f1": None if f1 is None else round(f1, 3), "exact_match": round(1 - len(details) / len(items), 3) if items else None,
            "errors": details[:10]}


async def eval_rag(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    qs = _load("rag_questions.json")
    answerable = [q for q in qs if not q["should_abstain"]]
    offtopic = [q for q in qs if q["should_abstain"]]
    correct = lost = avoided = 0
    cites_total = cites_right = section_hits = 0
    lat, errors = [], []
    for q in qs:
        r = await assistant.ask(db, question=q["question"], user={"id": "eval", "role": "system"}, log=False)
        lat.append(r["latency_ms"])
        cited = [s for s in r["sources"] if s["cited"]]
        if q["should_abstain"]:
            if r["abstained"]:
                avoided += 1
            else:
                errors.append({"id": q["id"], "problem": "answered an off-topic question"})
            continue
        if r["abstained"]:
            lost += 1
            errors.append({"id": q["id"], "problem": f"abstained ({r['abstain_reason']})"})
            continue
        cites_total += len(cited)
        cites_right += sum(1 for s in cited if s.get("slug") == q["expect_slug"])
        ok = any(s.get("slug") == q["expect_slug"] for s in cited)
        if ok:
            correct += 1
            if q.get("expect_section") and any(s.get("section") == q["expect_section"] for s in cited
                                               if s.get("slug") == q["expect_slug"]):
                section_hits += 1
        else:
            errors.append({"id": q["id"], "problem": "cited the wrong scheme",
                           "cited": sorted({s.get("slug") for s in cited})})
    with_section = [q for q in answerable if q.get("expect_section")]
    return {
        "n": len(qs), "mode": "extractive" if get_llm().name == "mock" else "generative",
        "correctness": round(correct / len(answerable), 3) if answerable else None,
        "section_match": round(section_hits / len(with_section), 3) if with_section else None,
        "citation_accuracy": round(cites_right / cites_total, 3) if cites_total else None,
        "abstention": {"off_topic_declined": round(avoided / len(offtopic), 3) if offtopic else None,
                       "answerable_lost": round(lost / len(answerable), 3) if answerable else None},
        "median_latency_ms": statistics.median(lat) if lat else None, "errors": errors[:15],
    }


async def eval_documents() -> dict[str, Any]:
    import tempfile

    from scripts.make_sample_docs import DOCS, make_pdf

    total = right = 0
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, (doc_type, expected) in EXPECTED_DOC_FIELDS.items():
            title, lines = DOCS[name]
            make_pdf(Path(tmp) / name, title, lines)
            ex = await doc_extraction.extract((Path(tmp) / name).read_bytes(), "application/pdf", doc_type)
            for f, v in expected.items():
                total += 1
                got = ex.fields.get(f, {}).get("value")
                if got == v or (isinstance(v, float) and isinstance(got, (int, float)) and abs(got - v) < 0.01):
                    right += 1
                else:
                    errors.append({"file": name, "field": f, "expected": v, "got": got})
    return {"documents": len(EXPECTED_DOC_FIELDS), "fields": total,
            "field_accuracy": round(right / total, 3) if total else None, "errors": errors[:15],
            "note": "Text-layer PDFs; scanned-image accuracy needs Tesseract and is not included."}


def eval_scam() -> dict[str, Any]:
    msgs = _load("scam_eval.json")
    tp = fp = fn = tn = 0
    errors = []
    for m in msgs:
        r = scam.classify(m["text"])
        pred = r["verdict"] != "consistent"
        truth = m["label"] == "scam"
        tp += pred and truth
        fp += pred and not truth
        fn += (not pred) and truth
        tn += (not pred) and not truth
        if pred != truth:
            errors.append({"text": m["text"][:90], "label": m["label"], "verdict": r["verdict"], "score": r["score"]})
    p = tp / (tp + fp) if tp + fp else None
    r_ = tp / (tp + fn) if tp + fn else None
    return {"n": len(msgs), "precision": None if p is None else round(p, 3), "recall": None if r_ is None else round(r_, 3),
            "accuracy": round((tp + tn) / len(msgs), 3) if msgs else None, "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
            "positive_means": "verdict is 'suspicious' or 'likely scam'", "errors": errors[:10]}


async def eval_interview(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    """Questions asked by the adaptive interview vs the full form, on personas built from the eligibility cases."""
    cases = _load("eligibility_cases.json")
    defaults = {"age": 35, "gender": "female", "state": "Bihar", "residence": "rural", "category": "general",
                "annual_income": 90000, "occupation": "unorganised_worker", "is_bpl": False, "marital_status": "married",
                "has_bank_account": True, "is_income_tax_payer": False, "is_govt_employee": False,
                "has_pucca_house": False, "disability_percent": 0, "family_size": 4, "education_level": "secondary",
                "is_minority": False, "monthly_income": 8000, "land_holding_hectares": 0, "marks_percent": 55,
                "is_pregnant_or_lactating": False, "has_girl_child_under_10": False, "has_lpg_connection": True,
                "has_epf_esic_nps": False}
    rows = await schemes.list_schemes(db)
    asked, agree = [], 0
    personas = [{**defaults, **c["profile"]} for c in cases[:30]]
    for persona in personas:
        answers: dict[str, Any] = {}
        while len(answers) < interview.MAX_QUESTIONS:
            f, _ = interview.choose_next(rows, answers, set())
            if f is None:
                break
            answers[f] = persona[f]
        asked.append(len(answers))
        full = {s["slug"] for s in rows if not (s["level"] == "state" and persona["state"] != s["state"])
                and eligibility.evaluate(s["rules"], persona).status == "eligible"}
        short = {s["slug"] for s in rows if not (s["level"] == "state" and answers.get("state") not in (None, s["state"]))
                 and eligibility.evaluate(s["rules"], answers).status == "eligible"}
        agree += full == short
    return {"personas": len(personas), "avg_questions_adaptive": round(statistics.mean(asked), 2) if asked else None,
            "questions_full_form": len(eligibility.FIELDS),
            "same_eligible_set": round(agree / len(personas), 3) if personas else None}


def eval_speech() -> dict[str, Any]:
    folder = EVAL_DIR / "speech"
    from app.ai import speech

    clips = sorted(folder.glob("*.wav")) if folder.exists() else []
    if not clips or not speech.status()["enabled"]:
        return {"measured": False, "reason": "No recorded samples in app/data/eval/speech/ (wav + matching txt)"
                if not clips else "Voice is disabled (ENABLE_VOICE=false)"}
    errs = words = 0
    for wav in clips:
        ref = (wav.with_suffix(".txt")).read_text(encoding="utf-8").split()
        hyp = speech.transcribe(wav.read_bytes(), "hi")["text"].split()
        d = list(range(len(hyp) + 1))
        for i, rw in enumerate(ref, 1):  # word-level edit distance
            prev, d[0] = d[0], i
            for j, hw in enumerate(hyp, 1):
                prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
        errs += d[len(hyp)]
        words += len(ref)
    return {"measured": True, "clips": len(clips), "wer": round(errs / words, 3) if words else None}


async def run(db: AsyncIOMotorDatabase, actor: dict[str, Any]) -> dict[str, Any]:
    t0 = time.perf_counter()
    metrics = {
        "eligibility": await eval_eligibility(db),
        "rule_extraction": eval_rule_extraction(),
        "rag": await eval_rag(db),
        "documents": await eval_documents(),
        "scam": eval_scam(),
        "interview": await eval_interview(db),
        "speech": eval_speech(),
    }
    run_doc = {"_id": uuid.uuid4().hex, "at": datetime.now(UTC), "by": actor.get("id"), "versions": versions(),
               "metrics": metrics, "seconds": round(time.perf_counter() - t0, 2)}
    await db.eval_runs.insert_one(run_doc)
    from app.services import audit

    await audit.record(db, actor=actor, action="ai.evaluation_run", after={"run": run_doc["_id"]})
    return {"id": run_doc["_id"], **{k: v for k, v in run_doc.items() if k != "_id"}}


async def history(db: AsyncIOMotorDatabase, limit: int = 10) -> list[dict[str, Any]]:
    return [{"id": r["_id"], **{k: v for k, v in r.items() if k != "_id"}}
            async for r in db.eval_runs.find().sort("at", -1).limit(limit)]
