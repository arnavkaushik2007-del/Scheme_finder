"""AI pre-check of an application: guidance only, never a decision.

It combines:
* the deterministic rules engine, run on the form answers against the scheme VERSION the
  application was opened under;
* a document checklist (required documents vs. documents attached);
* cross-document consistency (name, date of birth, state, Aadhaar last 4) between the form and the
  fields read from each attached document;
* risk flags (the same file uploaded by another account, documents never reviewed by the user).

The same report is the starting point for the officer's verification report (step 6).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services import eligibility, schemes

ENGINE_VERSION = "rules-v1"
PRECHECK_VERSION = "precheck-v1"

# A ration card is accepted as BPL proof; age proof can be satisfied by these.
EQUIVALENT = {"bpl_card": {"ration_card"}, "age_proof": {"aadhaar", "birth_certificate", "marksheet"},
              "address_proof": {"aadhaar", "domicile_certificate"}}


def _norm_name(name: str) -> set[str]:
    name = unicodedata.normalize("NFKC", name or "").lower()
    name = re.sub(r"\b(shri|smt|kumari|mr|mrs|ms|श्री|श्रीमती|कुमारी)\b\.?", " ", name)
    return {t for t in re.findall(r"[a-zऀ-ॿ]+", name) if len(t) > 1}


def names_match(a: str, b: str) -> bool:
    ta, tb = _norm_name(a), _norm_name(b)
    if not ta or not tb:
        return True
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.5


def _doc_value(doc: dict[str, Any], field: str) -> Any:
    if doc.get("confirmed_fields") and field in doc["confirmed_fields"]:
        return doc["confirmed_fields"][field]
    f = ((doc.get("extracted") or {}).get("fields") or {}).get(field)
    return f["value"] if f else None


def _item(code: str, level: str, en: str, hi: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "level": level, "en": en, "hi": hi, **extra}


async def run(db: AsyncIOMotorDatabase, app: dict[str, Any]) -> dict[str, Any]:
    version = await schemes.get_version(db, app["scheme_slug"], app["scheme_version"])
    if version is None:
        version = await db.schemes.find_one({"slug": app["scheme_slug"]})
    form = app.get("form", {})
    elig_in = form.get("eligibility", {})
    personal = form.get("personal", {})
    ev = eligibility.evaluate(version["rules"], elig_in)

    docs = [d async for d in db.documents.find({"_id": {"$in": app.get("documents", [])}, "user_id": app["user_id"],
                                                "deleted_at": None})]
    have = {d["doc_type"] for d in docs}
    catalog = {d["_id"]: d for d in [x async for x in db.required_documents.find()]}
    checklist = []
    for req in version.get("documents", []):
        ok = req in have or bool(EQUIVALENT.get(req, set()) & have)
        checklist.append({"id": req, "name": catalog.get(req, {}).get("name", {"en": req, "hi": req}), "attached": ok})
    missing_docs = [c for c in checklist if not c["attached"]]

    findings: list[dict[str, Any]] = []
    form_name = personal.get("full_name") or ""
    for d in docs:
        label = catalog.get(d["doc_type"], {}).get("name", {"en": d["doc_type"], "hi": d["doc_type"]})
        nm = _doc_value(d, "full_name")
        if nm and form_name and not names_match(nm, form_name):
            findings.append(_item("name_mismatch", "warning",
                                  f"Name on {label['en']} (“{nm}”) differs from the form (“{form_name}”).",
                                  f"{label['hi']} पर नाम (“{nm}”) फ़ॉर्म के नाम (“{form_name}”) से अलग है।",
                                  document=d["_id"]))
        dob = _doc_value(d, "date_of_birth")
        if dob and personal.get("date_of_birth") and dob != personal["date_of_birth"] and not str(dob).endswith("-07-01"):
            findings.append(_item("dob_mismatch", "warning",
                                  f"Date of birth on {label['en']} ({dob}) differs from the form ({personal['date_of_birth']}).",
                                  f"{label['hi']} पर जन्म तिथि ({dob}) फ़ॉर्म ({personal['date_of_birth']}) से अलग है।",
                                  document=d["_id"]))
        st = _doc_value(d, "state")
        if st and elig_in.get("state") and st != elig_in["state"]:
            findings.append(_item("state_mismatch", "warning",
                                  f"State on {label['en']} ({st}) differs from the form ({elig_in['state']}).",
                                  f"{label['hi']} पर राज्य ({st}) फ़ॉर्म ({elig_in['state']}) से अलग है।",
                                  document=d["_id"]))
        a4 = _doc_value(d, "aadhaar_last4")
        if a4 and personal.get("aadhaar_last4") and a4 != personal["aadhaar_last4"]:
            findings.append(_item("aadhaar_mismatch", "warning",
                                  "Aadhaar last 4 digits on the card differ from the form.",
                                  "कार्ड पर आधार के अंतिम 4 अंक फ़ॉर्म से अलग हैं।", document=d["_id"]))
        income = _doc_value(d, "annual_income")
        if income is not None and elig_in.get("annual_income") is not None and abs(int(income) - int(elig_in["annual_income"])) > 0.1 * max(1, int(income)):
            findings.append(_item("income_mismatch", "warning",
                                  f"Income on the certificate ({income}) differs from the form ({elig_in['annual_income']}).",
                                  f"प्रमाणपत्र पर आय ({income}) फ़ॉर्म ({elig_in['annual_income']}) से अलग है।",
                                  document=d["_id"]))
        if d.get("duplicate_across_accounts"):
            findings.append(_item("duplicate_file", "risk", "The same file was uploaded by another account.",
                                  "यही फ़ाइल किसी दूसरे खाते से भी अपलोड हुई है।", document=d["_id"]))
        if d.get("status") != "confirmed":
            findings.append(_item("unreviewed_document", "info",
                                  f"{label['en']}: fields were not reviewed by you yet.",
                                  f"{label['hi']}: आपने अभी फ़ील्ड की जाँच नहीं की है।", document=d["_id"]))

    warnings = [f for f in findings if f["level"] in ("warning", "risk")]
    if ev.status == "not_eligible":
        outcome = "likely_not_eligible"
    elif ev.status == "eligible" and not missing_docs and not warnings:
        outcome = "looks_complete"
    else:
        outcome = "needs_attention"
    score = (1.0 if ev.status == "eligible" else 0.5 if ev.status == "maybe" else 0.1)
    score *= (len(checklist) - len(missing_docs) + 1) / (len(checklist) + 1)
    score *= 0.8 ** len(warnings)
    return {
        "version": PRECHECK_VERSION, "engine": ENGINE_VERSION, "scheme_version": app["scheme_version"],
        "eligibility": ev.to_dict(), "documents": checklist, "missing_documents": [c["id"] for c in missing_docs],
        "findings": findings, "suggested": outcome, "score": round(score, 3),
        "note": {"en": "Guidance only. An officer makes the decision.",
                 "hi": "केवल मार्गदर्शन। निर्णय अधिकारी लेंगे।"},
        "ran_at": datetime.now(UTC),
    }
