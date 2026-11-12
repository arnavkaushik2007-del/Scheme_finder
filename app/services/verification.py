"""AI verification report for officers (spec 0.4). Advice only: the officer decides.

Built on the citizen pre-check, plus rule-by-rule EVIDENCE: for every eligibility rule, is the value
the applicant declared backed by an attached document, contradicted by one, or self-declared only?
The suggested outcome and confidence follow from that evidence.

Stored in ``ai_reports`` with the versions of every component, so a decision can always be traced
back to exactly what the officer was shown.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.ai.extraction import age_from_dob
from app.ai.llm import get_llm
from app.services import eligibility, precheck, schemes

log = logging.getLogger(__name__)

REPORT_VERSION = "verification-v1"

# Which document field can prove which eligibility field.
PROOF_FIELDS = {
    "age": "date_of_birth", "annual_income": "annual_income", "category": "category", "is_bpl": "is_bpl",
    "disability_percent": "disability_percent", "marks_percent": "marks_percent", "state": "state",
    "gender": "gender", "land_holding_hectares": "land_holding_hectares", "has_bank_account": "has_bank_account",
    "education_level": "education_level", "family_size": "family_size", "occupation": "occupation",
}


def _doc_value(doc: dict[str, Any], field: str) -> tuple[Any, float | None]:
    if doc.get("confirmed_fields") and field in doc["confirmed_fields"]:
        f = ((doc.get("extracted") or {}).get("fields") or {}).get(field) or {}
        return doc["confirmed_fields"][field], f.get("confidence")
    f = ((doc.get("extracted") or {}).get("fields") or {}).get(field)
    return (f["value"], f.get("confidence")) if f else (None, None)


def _as_of(app: dict[str, Any]) -> date:
    sub = app.get("submitted_at") or app.get("created_at")
    return (sub if isinstance(sub, datetime) else datetime.now(UTC)).date()


def _evidence_for(field: str, declared: Any, docs: list[dict[str, Any]], names: dict[str, Any],
                  as_of: date) -> dict[str, Any]:
    proof_field = PROOF_FIELDS.get(field)
    support, contra = [], []
    for d in docs:
        if not proof_field:
            break
        val, conf = _doc_value(d, proof_field)
        if val is None:
            continue
        if field == "age":
            val = age_from_dob(str(val), as_of)
            if val is None:
                continue
        label = names.get(d["doc_type"], {"en": d["doc_type"], "hi": d["doc_type"]})
        item = {"document": d["_id"], "doc_type": d["doc_type"], "doc_name": label, "value": val,
                "confidence": conf, "reviewed_by_applicant": d.get("status") == "confirmed"}
        matches = (declared is not None and (
            abs(float(val) - float(declared)) <= max(1.0, 0.1 * abs(float(val))) if isinstance(val, (int, float))
            and not isinstance(val, bool) and isinstance(declared, (int, float)) else val == declared))
        (support if matches else contra).append(item)
    status = "contradicted" if contra else "supported" if support else "self_declared"
    return {"status": status, "supporting": support, "contradicting": contra}


async def build(db: AsyncIOMotorDatabase, app: dict[str, Any]) -> dict[str, Any]:
    pc = await precheck.run(db, app)
    version = await schemes.get_version(db, app["scheme_slug"], app["scheme_version"]) or \
        await db.schemes.find_one({"slug": app["scheme_slug"]})
    declared = (app.get("form") or {}).get("eligibility", {})
    docs = [d async for d in db.documents.find({"_id": {"$in": app.get("documents", [])}, "user_id": app["user_id"]})]
    catalog = {d["_id"]: d["name"] async for d in db.required_documents.find()}
    as_of = _as_of(app)

    rules = []
    for leaf in eligibility.iter_leaves(version["rules"]):
        value = declared.get(leaf["field"])
        single = eligibility.evaluate({"all": [leaf]}, declared)
        ev = _evidence_for(leaf["field"], value, docs, catalog, as_of)
        # Re-check the rule with the documented value when a document contradicts the form.
        doc_outcome = None
        if ev["contradicting"]:
            doc_val = ev["contradicting"][0]["value"]
            doc_outcome = eligibility.evaluate({"all": [leaf]}, {**declared, leaf["field"]: doc_val}).status
        rules.append({
            "id": leaf["id"], "field": leaf["field"],
            "text": {"en": eligibility.describe_rule(leaf, "en"), "hi": eligibility.describe_rule(leaf, "hi")},
            "declared": value, "outcome": {"eligible": "pass", "not_eligible": "fail", "maybe": "unknown"}[single.status],
            "evidence": ev["status"], "supporting": ev["supporting"], "contradicting": ev["contradicting"],
            "outcome_with_document_value": doc_outcome,
        })

    risk = [f for f in pc["findings"] if f["level"] == "risk"]
    mismatches = [f for f in pc["findings"] if f["code"].endswith("_mismatch")]
    contradicted = [r for r in rules if r["evidence"] == "contradicted"]
    flips = [r for r in contradicted if r["outcome_with_document_value"] == "not_eligible"]
    failed = [r for r in rules if r["outcome"] == "fail"]
    unknown = [r for r in rules if r["outcome"] == "unknown"]
    supported = [r for r in rules if r["evidence"] == "supported"]
    status = pc["eligibility"]["status"]

    reasons: list[dict[str, str]] = []
    if status == "not_eligible":
        suggested = "not_eligible"
        reasons += [{"en": f"Rule not met: {r['text']['en']}", "hi": f"नियम पूरा नहीं: {r['text']['hi']}"} for r in failed]
    elif flips:
        suggested = "not_eligible"
        reasons += [{"en": f"A document contradicts the form and the rule fails with the documented value: {r['text']['en']}",
                     "hi": f"दस्तावेज़ फ़ॉर्म से अलग है और दस्तावेज़ के मान से नियम पूरा नहीं होता: {r['text']['hi']}"} for r in flips]
    elif status == "eligible" and not pc["missing_documents"] and not risk and not mismatches and not contradicted:
        suggested = "eligible"
        reasons.append({"en": "All rules met and the required documents are attached.",
                        "hi": "सभी नियम पूरे हैं और ज़रूरी दस्तावेज़ लगे हैं।"})
    else:
        suggested = "needs_review"
        if unknown:
            reasons.append({"en": f"{len(unknown)} rule(s) cannot be checked: information missing.",
                            "hi": f"{len(unknown)} नियम जाँचे नहीं जा सकते: जानकारी नहीं है।"})
        if pc["missing_documents"]:
            reasons.append({"en": f"{len(pc['missing_documents'])} required document(s) missing.",
                            "hi": f"{len(pc['missing_documents'])} ज़रूरी दस्तावेज़ नहीं लगे।"})
        if mismatches or contradicted:
            reasons.append({"en": "Some details differ between the form and the documents.",
                            "hi": "फ़ॉर्म और दस्तावेज़ों में कुछ जानकारी अलग है।"})
        if risk:
            reasons.append({"en": "Risk flags present (see below).", "hi": "जोखिम संकेत मौजूद हैं (नीचे देखें)।"})

    total = max(1, len(rules))
    evidence_share = len(supported) / total
    docs_share = 1 - len(pc["missing_documents"]) / max(1, len(pc["documents"]))
    confidence = 0.35 + 0.35 * evidence_share + 0.2 * docs_share - 0.1 * len(risk) - 0.05 * len(mismatches)
    if suggested == "not_eligible" and failed:
        confidence = 0.75 + 0.2 * (len([r for r in failed if r["evidence"] == "supported"]) / len(failed))
    confidence = round(max(0.05, min(0.98, confidence)), 3)

    summary = _summary(app, pc, rules, suggested, confidence)
    llm = get_llm()
    return {
        "version": REPORT_VERSION, "precheck_version": pc["version"], "engine": pc["engine"],
        "scheme_version": app["scheme_version"], "llm": llm.name, "generated_at": datetime.now(UTC),
        "eligibility_status": status, "rules": rules, "documents": pc["documents"],
        "missing_documents": pc["missing_documents"], "mismatches": mismatches, "risk_flags": risk,
        "other_findings": [f for f in pc["findings"] if f not in mismatches and f not in risk],
        "suggested": suggested, "confidence": confidence, "reasons": reasons, "summary": summary,
        "note": {"en": "Advice only. The officer's decision is the decision.",
                 "hi": "केवल सलाह। निर्णय अधिकारी का है।"},
    }


def _summary(app: dict[str, Any], pc: dict[str, Any], rules: list[dict[str, Any]], suggested: str,
             confidence: float) -> dict[str, str]:
    """Deterministic case summary built from the report (no free text from a model)."""
    n_pass = sum(r["outcome"] == "pass" for r in rules)
    n_sup = sum(r["evidence"] == "supported" for r in rules)
    label = {"eligible": ("eligible", "पात्र"), "not_eligible": ("not eligible", "पात्र नहीं"),
             "needs_review": ("needs review", "समीक्षा ज़रूरी")}[suggested]
    en = (f"{app['scheme_name']['en']}: {n_pass} of {len(rules)} rules met; {n_sup} backed by documents. "
          f"{len(pc['missing_documents'])} required document(s) missing, {len(pc['findings'])} finding(s). "
          f"AI suggestion: {label[0]} (confidence {round(confidence * 100)}%).")
    hi = (f"{app['scheme_name']['hi']}: {len(rules)} में से {n_pass} नियम पूरे; {n_sup} दस्तावेज़ों से पुष्ट। "
          f"{len(pc['missing_documents'])} ज़रूरी दस्तावेज़ नहीं, {len(pc['findings'])} टिप्पणियाँ। "
          f"AI सुझाव: {label[1]} (विश्वसनीयता {round(confidence * 100)}%)।")
    return {"en": en, "hi": hi}


async def generate_and_store(db: AsyncIOMotorDatabase, app: dict[str, Any]) -> dict[str, Any]:
    report = await build(db, app)
    rid = uuid.uuid4().hex
    await db.ai_reports.insert_one({"_id": rid, "application_id": app["_id"], "created_at": report["generated_at"],
                                    "versions": {k: report[k] for k in ("version", "precheck_version", "engine",
                                                                        "scheme_version", "llm")},
                                    "report": report})
    await db.applications.update_one({"_id": app["_id"]}, {"$set": {"latest_report_id": rid,
                                                                    "ai_suggested": report["suggested"],
                                                                    "ai_confidence": report["confidence"]}})
    return {"id": rid, **report}


async def latest(db: AsyncIOMotorDatabase, app: dict[str, Any]) -> dict[str, Any] | None:
    if not app.get("latest_report_id"):
        return None
    row = await db.ai_reports.find_one({"_id": app["latest_report_id"]})
    return {"id": row["_id"], **row["report"]} if row else None
