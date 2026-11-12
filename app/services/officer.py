"""Officer work: queue, case view, AI report, decisions (with AI agreement tracking), history, stats.

An officer sees only applications assigned to them (or unassigned ones they can claim). Every
document view and every decision is written to the audit log.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services import applications as apps
from app.services import audit, documents, verification
from app.services.errors import ServiceError

OPEN = ("precheck_done", "under_review", "more_docs_needed", "resubmitted")
ACTION_TO_STATUS = {"approve": "approved", "reject": "rejected", "request_docs": "more_docs_needed"}
AI_EQUIVALENT = {"approve": "eligible", "reject": "not_eligible", "request_docs": "needs_review"}


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: Any) -> datetime | None:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _age_days(app: dict[str, Any]) -> float:
    start = _aware(app.get("submitted_at")) or _aware(app.get("created_at")) or _now()
    return round((_now() - start).total_seconds() / 86400, 1)


async def _applicant(db: AsyncIOMotorDatabase, app: dict[str, Any]) -> dict[str, Any]:
    p = (app.get("form") or {}).get("personal", {})
    e = (app.get("form") or {}).get("eligibility", {})
    return {"full_name": p.get("full_name") or "—", "date_of_birth": p.get("date_of_birth"), "age": e.get("age"),
            "gender": e.get("gender"), "state": e.get("state"), "district": p.get("district"),
            "address": p.get("address"),
            "aadhaar_masked": f"XXXX XXXX {p['aadhaar_last4']}" if p.get("aadhaar_last4") else None,
            "bank_masked": f"XXXXXX{p['bank_account_last4']}" if p.get("bank_account_last4") else None}


def _row(app: dict[str, Any], name: str) -> dict[str, Any]:
    return {"id": app["_id"], "scheme_slug": app["scheme_slug"], "scheme_name": app["scheme_name"],
            "applicant": name, "status": app["status"], "status_text": apps.STATUS_TEXT[app["status"]],
            "ai_suggested": app.get("ai_suggested"), "ai_confidence": app.get("ai_confidence"),
            "age_days": _age_days(app), "submitted_at": app.get("submitted_at"), "updated_at": app.get("updated_at")}


async def queue(db: AsyncIOMotorDatabase, officer_id: str, *, status: str | None = None, scheme: str | None = None,
                min_conf: float | None = None, max_conf: float | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {"assigned_officer_id": officer_id, "status": {"$in": list(OPEN)}}
    if status:
        q["status"] = status
    if scheme:
        q["scheme_slug"] = scheme
    rows = []
    async for a in db.applications.find(q):
        conf = a.get("ai_confidence")
        if min_conf is not None and (conf is None or conf < min_conf):
            continue
        if max_conf is not None and (conf is None or conf > max_conf):
            continue
        rows.append(_row(a, (a.get("form") or {}).get("personal", {}).get("full_name") or "—"))
    rows.sort(key=lambda r: -r["age_days"])  # oldest first
    counts = {s: await db.applications.count_documents({"assigned_officer_id": officer_id, "status": s}) for s in OPEN}
    week_ago = _now().timestamp() - 7 * 86400
    decided = [d async for d in db.decisions.find({"officer_id": officer_id})]
    counts["decided_7d"] = sum(1 for d in decided if _aware(d["at"]) and _aware(d["at"]).timestamp() >= week_ago
                               and d["action"] != "request_docs")
    unassigned = [_row(a, (a.get("form") or {}).get("personal", {}).get("full_name") or "—")
                  async for a in db.applications.find({"assigned_officer_id": None, "status": {"$in": list(OPEN)}})]
    return {"items": rows, "counts": counts, "unassigned": unassigned}


async def _assigned(db: AsyncIOMotorDatabase, officer_id: str, app_id: str) -> dict[str, Any]:
    app = await db.applications.find_one({"_id": app_id, "assigned_officer_id": officer_id})
    if not app:
        raise ServiceError("application_not_found", 404)
    return app


async def claim(db: AsyncIOMotorDatabase, actor: dict[str, Any], app_id: str) -> dict[str, Any]:
    res = await db.applications.update_one({"_id": app_id, "assigned_officer_id": None, "status": {"$in": list(OPEN)}},
                                           {"$set": {"assigned_officer_id": actor["id"], "updated_at": _now()}})
    if res.modified_count == 0:
        raise ServiceError("application_not_found", 404)
    await audit.record(db, actor=actor, action="application.claim", target={"type": "application", "id": app_id})
    return await case(db, actor["id"], app_id)


async def case(db: AsyncIOMotorDatabase, officer_id: str, app_id: str) -> dict[str, Any]:
    app = await _assigned(db, officer_id, app_id)
    report = await verification.latest(db, app) or await verification.generate_and_store(db, app)
    docs = [{"id": d["_id"], "doc_type": d["doc_type"], "filename": d.get("filename"), "content_type": d.get("content_type"),
             "status": d.get("status"), "extracted": d.get("extracted"), "confirmed_fields": d.get("confirmed_fields"),
             "deleted": d.get("deleted_at") is not None, "duplicate_across_accounts": d.get("duplicate_across_accounts")}
            async for d in db.documents.find({"_id": {"$in": app.get("documents", [])}})]
    decisions = [{k: v for k, v in d.items() if k != "_id"} async for d in
                 db.decisions.find({"application_id": app_id}).sort("at", 1)]
    return {"application": {**_row(app, (app.get("form") or {}).get("personal", {}).get("full_name") or "—"),
                            "scheme_version": app["scheme_version"], "form": app.get("form"),
                            "history": app.get("history", []), "documents": app.get("documents", [])},
            "applicant": await _applicant(db, app), "documents": docs, "report": report, "decisions": decisions}


async def read_document(db: AsyncIOMotorDatabase, actor: dict[str, Any], app_id: str,
                        doc_id: str) -> tuple[bytes, str]:
    app = await _assigned(db, actor["id"], app_id)
    if doc_id not in app.get("documents", []):
        raise ServiceError("document_not_found", 404)
    doc = await db.documents.find_one({"_id": doc_id, "deleted_at": None})
    if not doc:
        raise ServiceError("document_not_found", 404)
    data, ctype = await documents.read_file(db, doc["user_id"], doc_id)
    await audit.record(db, actor=actor, action="document.view", target={"type": "document", "id": doc_id},
                       after={"application": app_id, "doc_type": doc["doc_type"]})
    return data, ctype


async def start_review(db: AsyncIOMotorDatabase, actor: dict[str, Any], app_id: str) -> dict[str, Any]:
    app = await _assigned(db, actor["id"], app_id)
    await apps.transition(db, app_id, "under_review", actor=actor)
    await verification.generate_and_store(db, await db.applications.find_one({"_id": app_id}))
    _ = app
    return await case(db, actor["id"], app_id)


async def regenerate_report(db: AsyncIOMotorDatabase, actor: dict[str, Any], app_id: str) -> dict[str, Any]:
    app = await _assigned(db, actor["id"], app_id)
    return await verification.generate_and_store(db, app)


async def decide(db: AsyncIOMotorDatabase, actor: dict[str, Any], app_id: str, action: str, reason: str,
                 requested_documents: list[str]) -> dict[str, Any]:
    app = await _assigned(db, actor["id"], app_id)
    if action not in ACTION_TO_STATUS:
        raise ServiceError("invalid_action", 422)
    report = await verification.latest(db, app)
    ai = report["suggested"] if report else None
    differs = ai is not None and AI_EQUIVALENT[action] != ai
    note = reason
    if action == "request_docs" and requested_documents:
        names = {d["_id"]: d["name"]["en"] async for d in db.required_documents.find({"_id": {"$in": requested_documents}})}
        note = f"{reason} — Documents: {', '.join(names.get(d, d) for d in requested_documents)}"
    updated = await apps.transition(db, app_id, ACTION_TO_STATUS[action], actor=actor, note=note,
                                    extra={"decision": {"action": action, "reason": reason, "at": _now(),
                                                        "officer_id": actor["id"]}} if action != "request_docs" else None)
    started = _aware(app.get("submitted_at")) or _aware(app.get("created_at"))
    record = {"_id": uuid.uuid4().hex, "application_id": app_id, "officer_id": actor["id"], "action": action,
              "reason": reason, "requested_documents": requested_documents, "at": _now(),
              "ai_suggested": ai, "ai_confidence": report["confidence"] if report else None,
              "report_id": report["id"] if report else None, "differs_from_ai": differs,
              "scheme_slug": app["scheme_slug"], "scheme_version": app["scheme_version"],
              "hours_since_submit": round((_now() - started).total_seconds() / 3600, 2) if started else None}
    await db.decisions.insert_one(record)
    return {"application": apps.public(updated, for_role="officer"),
            "decision": {k: v for k, v in record.items() if k != "_id"}}


async def close(db: AsyncIOMotorDatabase, actor: dict[str, Any], app_id: str) -> dict[str, Any]:
    await _assigned(db, actor["id"], app_id)
    return apps.public(await apps.transition(db, app_id, "closed", actor=actor), for_role="officer")


async def history(db: AsyncIOMotorDatabase, officer_id: str) -> list[dict[str, Any]]:
    out = []
    async for d in db.decisions.find({"officer_id": officer_id}).sort("at", -1).limit(200):
        a = await db.applications.find_one({"_id": d["application_id"]}, {"scheme_name": 1, "form.personal.full_name": 1,
                                                                          "status": 1})
        out.append({**{k: v for k, v in d.items() if k != "_id"},
                    "scheme_name": (a or {}).get("scheme_name"),
                    "applicant": ((a or {}).get("form") or {}).get("personal", {}).get("full_name"),
                    "current_status": (a or {}).get("status")})
    return out


async def stats(db: AsyncIOMotorDatabase, officer_id: str) -> dict[str, Any]:
    ds = [d async for d in db.decisions.find({"officer_id": officer_id})]
    final = [d for d in ds if d["action"] != "request_docs"]
    with_ai = [d for d in ds if d.get("ai_suggested")]
    hours = [d["hours_since_submit"] for d in final if d.get("hours_since_submit") is not None]
    by_action = {a: sum(1 for d in ds if d["action"] == a) for a in ACTION_TO_STATUS}
    return {
        "decisions": len(ds), "cases_decided": len(final), "by_action": by_action,
        "avg_hours_to_decision": round(sum(hours) / len(hours), 2) if hours else None,
        "override_rate": round(sum(d["differs_from_ai"] for d in with_ai) / len(with_ai), 3) if with_ai else None,
        "agreement_by_ai_suggestion": {
            s: {"total": sum(1 for d in with_ai if d["ai_suggested"] == s),
                "agreed": sum(1 for d in with_ai if d["ai_suggested"] == s and not d["differs_from_ai"])}
            for s in ("eligible", "not_eligible", "needs_review")},
        "open_cases": await db.applications.count_documents({"assigned_officer_id": officer_id,
                                                             "status": {"$in": list(OPEN)}}),
    }
