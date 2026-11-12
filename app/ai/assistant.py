"""The assistant: retrieve -> answer with citations -> hallucination guard -> answer or abstain.

Two answer modes:
* ``generative`` - a configured LLM writes the answer from numbered sources and must cite them.
* ``extractive`` - no LLM configured: the answer is made of sentences copied from the best sources,
  each with its citation. Nothing is invented, and the UI labels the mode.

Eligibility questions are never answered by the model: if the person asks whether they qualify,
the deterministic rules engine is run on their saved profile and its result becomes a cited source.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.ai import guard, index
from app.ai.embeddings import get_embedder
from app.ai.extraction import mask_ids
from app.ai.llm import get_llm
from app.config import get_settings
from app.services import eligibility, schemes

log = logging.getLogger(__name__)

PORTAL = "https://www.myscheme.gov.in"
ELIGIBILITY_Q = re.compile(r"(?i)eligib|qualif|can i (get|apply)|am i|पात्र|मिल सकता|मिलेगा|मिलेगी|ले सकत|योग्य")
STATUS_TEXT = {"eligible": {"en": "likely eligible", "hi": "संभवतः पात्र"},
               "maybe": {"en": "maybe eligible (some information is missing)", "hi": "शायद पात्र (कुछ जानकारी बाकी है)"},
               "not_eligible": {"en": "not eligible", "hi": "पात्र नहीं"}}


def detect_language(text: str) -> str:
    letters = re.findall(r"[A-Za-zऀ-ॿ]", text)
    if not letters:
        return "en"
    deva = sum(1 for ch in letters if "ऀ" <= ch <= "ॿ")
    return "hi" if deva / len(letters) >= 0.3 else "en"


def _norm_retrieval(score: float) -> float:
    e = get_embedder()
    return max(0.0, min(1.0, (score - e.abstain_below) / max(1e-6, e.strong_above - e.abstain_below)))


def _abstain_text(lang: str, url: str) -> str:
    if lang == "hi":
        return (f"मुझे उपलब्ध जानकारी से इसका भरोसेमंद जवाब नहीं मिला। कृपया आधिकारिक पोर्टल पर जाँचें: {url}")
    return f"I'm not sure about this from the information I have. Please check the official portal: {url}"


async def _engine_source(db: AsyncIOMotorDatabase, slug: str, profile: dict[str, Any], lang: str) -> str | None:
    try:
        res = await schemes.check_one(db, slug, profile)
    except Exception:  # noqa: BLE001 - scheme removed
        return None
    s = await db.schemes.find_one({"slug": slug}, {"name": 1, "rules": 1})
    leaves = {r["id"]: r for r in eligibility.iter_leaves(s["rules"])}

    def names(ids: list[str]) -> str:
        return "; ".join(eligibility.describe_rule(leaves[i], lang) for i in ids) or "—"

    if lang == "hi":
        return (f"नियम-इंजन का परिणाम ({s['name']['hi']}, आपकी सहेजी प्रोफ़ाइल से): {STATUS_TEXT[res['status']]['hi']}। "
                f"पूरे हुए नियम: {names(res['passed'])}। पूरे नहीं हुए: {names(res['failed'])}। "
                f"जानकारी बाकी: {names(res['unknown'])}। यह अनुमान है, अंतिम निर्णय अधिकारी का होगा।")
    return (f"Rules engine result for {s['name']['en']} (from your saved profile): {STATUS_TEXT[res['status']]['en']}. "
            f"Rules met: {names(res['passed'])}. Rules not met: {names(res['failed'])}. "
            f"Information missing: {names(res['unknown'])}. This is an estimate; the officer makes the final decision.")


GEN_SYSTEM = """You answer questions about Indian government welfare schemes for a prototype assistant.
Rules:
- Use ONLY the numbered sources. Do not use outside knowledge.
- End every sentence with the source id(s) it relies on, like [S2] or [S1][S3].
- Copy numbers (amounts, ages, percentages) exactly as they appear in the sources.
- Never say someone is or will be approved. Eligibility verdicts come only from a source labelled "Rules engine".
- Reply in {lang_name}. Keep it short: at most 5 sentences, plain language.
- If the sources do not answer the question, reply with exactly: NOT_FOUND"""


INTENTS = {
    "documents": re.compile(r"(?i)document|papers?|certificate|proof|दस्तावेज़|दस्तावेज|कागज|प्रमाण"),
    "steps": re.compile(r"(?i)\bapply\b|how (do|can|to)|register|process|steps?|आवेदन|कैसे|प्रक्रिया"),
    "rules": re.compile(r"(?i)eligib|qualif|age|limit|who can|criteria|income|पात्र|आयु|उम्र|सीमा|कौन|शर्त|आय"),
    "overview": re.compile(r"(?i)benefit|how much|amount|money|what is|get\b|लाभ|कितना|कितने|राशि|पैसा|क्या है"),
}


def detect_intent(question: str) -> str | None:
    for name, rx in INTENTS.items():
        if rx.search(question):
            return name
    return None


def _extractive(question: str, sources: list[tuple[int, str, str]]) -> str:
    """Copy the most relevant sentences from the best sources, each with its citation."""
    qt = set(index.tokens(question))
    picked: list[tuple[int, str]] = []
    for rank, (n, text, section) in enumerate(sources[:3]):
        sents = [s for s in guard.split_sentences(text) if len(index.tokens(s)) >= 4]
        if section == "rules_engine":
            picked += [(n, s) for s in sents[:2]]
            continue
        scored = sorted(((len(qt & set(index.tokens(s))), i, s) for i, s in enumerate(sents)), key=lambda x: (-x[0], x[1]))
        take = 2 if rank == 0 else 1
        for score, _, s in scored[:take]:
            if score > 0 or rank == 0:
                picked.append((n, s))
        if len(picked) >= 3:
            break
    return " ".join(f"{s.rstrip()} [S{n}]" for n, s in picked[:4])


async def ask(db: AsyncIOMotorDatabase, *, question: str, user: dict[str, Any], scheme_slug: str | None = None,
              profile: dict[str, Any] | None = None, log: bool = True) -> dict[str, Any]:
    t0 = time.perf_counter()
    settings = get_settings()
    lang = detect_language(question)
    q_masked = mask_ids(question.strip())[:1000]
    hits = await index.search(db, q_masked, k=6, slug=scheme_slug, lang=lang)
    top_vec = max((h.meta.get("vector_score", 0.0) for h in hits), default=0.0)
    relevant = hits and top_vec >= get_embedder().abstain_below and hits[0].meta.get("url", "").startswith("http")
    url = hits[0].meta["url"] if relevant else PORTAL
    if scheme_slug:
        s = await db.schemes.find_one({"slug": scheme_slug}, {"official_url": 1})
        url = s["official_url"] if s else url

    # Numbered sources: rules-engine result first (if relevant), then retrieved chunks in the user's language.
    sources: list[dict[str, Any]] = []
    target_slug = scheme_slug or (hits[0].meta.get("slug") if hits and top_vec >= get_embedder().abstain_below else None)
    if ELIGIBILITY_Q.search(question) and target_slug and profile:
        text = await _engine_source(db, target_slug, profile, lang)
        if text:
            sources.append({"id": f"engine:{target_slug}", "text": text, "title": {"en": "Rules engine", "hi": "नियम-इंजन"},
                            "section": "rules_engine", "url": f"/scheme/{target_slug}", "slug": target_slug,
                            "lang": lang, "score": 1.0})
    preferred = [h for h in hits if h.meta.get("lang") == lang] or hits
    intent = detect_intent(question)
    if intent and preferred:
        # Within the best-matching scheme, put the section that answers this kind of question first.
        best_slug = preferred[0].meta.get("slug")
        preferred.sort(key=lambda h: (h.meta.get("slug") != best_slug, h.meta.get("section") != intent))
    for h in preferred[:5]:
        sources.append({"id": h.id, "text": h.text, "title": {"en": h.meta.get("title_en"), "hi": h.meta.get("title_hi")},
                        "section": h.meta.get("section"), "url": h.meta.get("url"), "slug": h.meta.get("slug"),
                        "lang": h.meta.get("lang"), "score": round(h.meta.get("vector_score", 0.0), 3)})
    numbered = {i + 1: s["text"] for i, s in enumerate(sources)}

    llm = get_llm()
    mode = "extractive" if llm.name == "mock" else "generative"
    retrieval = _norm_retrieval(top_vec)
    answer, guard_res, abstained, reason = "", None, False, None

    if top_vec < get_embedder().abstain_below and not any(s["section"] == "rules_engine" for s in sources):
        abstained, reason = True, "low_retrieval"
    else:
        if mode == "generative":
            src_block = "\n\n".join(f"[S{n}] {t}" for n, t in numbered.items())
            res = await llm.complete(GEN_SYSTEM.format(lang_name="Hindi" if lang == "hi" else "English"),
                                     f"<sources>\n{src_block}\n</sources>\n\n<question>{q_masked}</question>",
                                     max_tokens=900)
            draft = res.text.strip() if not res.error else ""
            if not draft or "NOT_FOUND" in draft:
                abstained, reason = True, "model_not_found" if not res.error else f"model_error:{res.error}"
        else:
            draft = _extractive(q_masked, [(i + 1, s["text"], s["section"]) for i, s in enumerate(sources)])
            if not draft:
                abstained, reason = True, "no_matching_sentence"
        if not abstained:
            guard_res = await guard.check(draft, numbered)
            kept = guard_res.kept
            if not kept or guard_res.supported_fraction < 0.5:
                abstained, reason = True, "guard_failed"
            else:
                answer = " ".join(f"{c.text} " + "".join(f"[S{n}]" for n in c.cites) for c in kept)

    support = guard_res.supported_fraction if guard_res else 0.0
    engine_used = any(s["section"] == "rules_engine" for s in sources)
    confidence = round(0.4 * (1.0 if engine_used else retrieval) + 0.6 * support, 3) if not abstained else 0.0
    if not abstained and confidence < settings.assistant_min_confidence:
        abstained, reason = True, "low_confidence"
    if abstained:
        answer = _abstain_text(lang, url)

    cited = sorted({n for n in map(int, guard.CITE_RE.findall(answer))}) if not abstained else []
    log_id = uuid.uuid4().hex
    out = {
        "id": log_id, "answer": answer, "language": lang, "abstained": abstained, "abstain_reason": reason,
        "confidence": confidence, "mode": mode, "model": llm.name if mode == "generative" else "none",
        "embedder": get_embedder().name, "official_url": url,
        "sources": [{**s, "n": i + 1, "cited": (i + 1) in cited} for i, s in enumerate(sources)],
        "guard": guard_res.to_dict() if guard_res else None,
        "latency_ms": round((time.perf_counter() - t0) * 1000),
    }
    if log:
        await db.chat_logs.insert_one({
            "_id": log_id, "user_id": user["id"], "role": user["role"], "question": q_masked, "answer": answer,
            "language": lang, "scheme_slug": scheme_slug, "abstained": abstained, "abstain_reason": reason,
            "confidence": confidence, "mode": mode, "model": out["model"], "embedder": out["embedder"],
            "sources": [s["id"] for s in sources], "cited": cited, "guard": out["guard"], "latency_ms": out["latency_ms"],
            "feedback": None, "created_at": datetime.now(UTC),
        })
    return out
