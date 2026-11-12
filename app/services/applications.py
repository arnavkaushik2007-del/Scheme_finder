"""Application lifecycle (spec 0.6).

    draft -> submitted -> precheck_done -> under_review -> approved / rejected -> closed
                                              |   ^
                                              v   |
                                   more_docs_needed -> resubmitted
    withdrawn: by the citizen at any point before a decision.

Every change appends to ``history`` (who, when, from, to, note) and sends the citizen a
notification. Transitions not in ``TRANSITIONS`` are refused, whoever asks.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services import audit, precheck, profiles
from app.services.errors import ServiceError

log = logging.getLogger(__name__)

STATUSES = ("draft", "submitted", "precheck_done", "under_review", "more_docs_needed", "resubmitted",
            "approved", "rejected", "closed", "withdrawn")
ACTIVE = ("draft", "submitted", "precheck_done", "under_review", "more_docs_needed", "resubmitted")
DECIDED = ("approved", "rejected")

# (from, to) -> roles allowed to make that move
TRANSITIONS: dict[tuple[str, str], set[str]] = {
    ("draft", "submitted"): {"user"},
    ("submitted", "precheck_done"): {"system"},
    ("precheck_done", "under_review"): {"officer"},
    ("resubmitted", "under_review"): {"officer"},
    ("under_review", "more_docs_needed"): {"officer"},
    ("more_docs_needed", "resubmitted"): {"user"},
    ("under_review", "approved"): {"officer"},
    ("under_review", "rejected"): {"officer"},
    ("approved", "closed"): {"officer", "system"},
    ("rejected", "closed"): {"officer", "system"},
    **{(s, "withdrawn"): {"user"} for s in ACTIVE},
}

STATUS_TEXT = {
    "draft": {"en": "Draft", "hi": "ड्राफ़्ट"},
    "submitted": {"en": "Submitted", "hi": "जमा किया गया"},
    "precheck_done": {"en": "AI pre-check done, waiting for an officer", "hi": "AI पूर्व-जाँच पूरी, अधिकारी की प्रतीक्षा"},
    "under_review": {"en": "Under officer review", "hi": "अधिकारी समीक्षा कर रहे हैं"},
    "more_docs_needed": {"en": "More documents needed", "hi": "और दस्तावेज़ चाहिए"},
    "resubmitted": {"en": "Resubmitted", "hi": "दोबारा जमा किया गया"},
    "approved": {"en": "Approved", "hi": "स्वीकृत"},
    "rejected": {"en": "Rejected", "hi": "अस्वीकृत"},
    "closed": {"en": "Closed", "hi": "बंद"},
    "withdrawn": {"en": "Withdrawn", "hi": "वापस लिया गया"},
}


def _now() -> datetime:
    return datetime.now(UTC)


def public(app: dict[str, Any], *, for_role: str = "user") -> dict[str, Any]:
    out = {k: v for k, v in app.items() if k not in ("_id",)}
    out["id"] = app["_id"]
    out["status_text"] = STATUS_TEXT[app["status"]]
    if for_role == "user":
        for k in ("assigned_officer_id", "latest_report_id", "ai_suggested", "ai_confidence"):
            out.pop(k, None)
    return out


async def notify(db: AsyncIOMotorDatabase, app: dict[str, Any], status: str, note: str | None) -> None:
    title = {"en": f"{app['scheme_name']['en']}: {STATUS_TEXT[status]['en']}",
             "hi": f"{app['scheme_name']['hi']}: {STATUS_TEXT[status]['hi']}"}
    await db.notifications.insert_one({"_id": uuid.uuid4().hex, "user_id": app["user_id"], "application_id": app["_id"],
                                       "status": status, "title": title, "note": note, "read": False,
                                       "created_at": _now(), "seq": time.time_ns()})


async def transition(db: AsyncIOMotorDatabase, app_id: str, to: str, *, actor: dict[str, Any], note: str | None = None,
                     extra: dict[str, Any] | None = None) -> dict[str, Any]:
    app = await db.applications.find_one({"_id": app_id})
    if not app:
        raise ServiceError("application_not_found", 404)
    frm = app["status"]
    allowed = TRANSITIONS.get((frm, to))
    if not allowed or actor["role"] not in allowed:
        raise ServiceError("invalid_transition", 409, from_status=frm, to_status=to)
    entry = {"from": frm, "to": to, "at": _now(), "by": {"role": actor["role"], "id": actor.get("id")}, "note": note}
    update: dict[str, Any] = {"status": to, "updated_at": _now(), **(extra or {})}
    if to in DECIDED:
        update["decided_at"] = _now()
    res = await db.applications.update_one({"_id": app_id, "status": frm},
                                           {"$set": update, "$push": {"history": entry}})
    if res.modified_count == 0:  # someone else moved it first
        raise ServiceError("invalid_transition", 409, from_status=frm, to_status=to)
    app = await db.applications.find_one({"_id": app_id})
    await notify(db, app, to, note)
    if actor["role"] in ("officer", "admin"):
        await audit.record(db, actor=actor, action=f"application.{to}", target={"type": "application", "id": app_id},
                           before={"status": frm}, after={"status": to}, reason=note)
    return app

# ----------------------------------------------------------------------------- assignment


async def pick_officer(db: AsyncIOMotorDatabase, scheme_slug: str, state: str | None) -> str | None:
    """Least-loaded active officer, preferring officers assigned to this scheme, then to this state."""
    officers = []
    async for o in db.officers.find():
        u = await db.users.find_one({"_id": o["user_id"], "status": "active", "deleted_at": None})
        if not u:
            continue
        load = await db.applications.count_documents({"assigned_officer_id": o["user_id"],
                                                      "status": {"$in": list(ACTIVE)}})
        pref = 0 if scheme_slug in (o.get("assigned_schemes") or []) else 1 if state and o.get("region") == state else 2
        officers.append((pref, load, o["user_id"]))
    if not officers:
        return None
    officers.sort()
    return officers[0][2]

# ----------------------------------------------------------------------------- citizen actions


async def _scheme(db: AsyncIOMotorDatabase, slug: str) -> dict[str, Any]:
    s = await db.schemes.find_one({"slug": slug, "deleted_at": None})
    if not s:
        raise ServiceError("scheme_not_found", 404)
    return s


async def _validate_docs(db: AsyncIOMotorDatabase, user_id: str, doc_ids: list[str]) -> list[str]:
    ids = list(dict.fromkeys(doc_ids))
    found = await db.documents.count_documents({"_id": {"$in": ids}, "user_id": user_id, "deleted_at": None})
    if found != len(ids):
        raise ServiceError("document_not_found", 404)
    return ids


async def create_draft(db: AsyncIOMotorDatabase, user_id: str, slug: str, form: dict[str, Any],
                       documents: list[str]) -> dict[str, Any]:
    await profiles.require_save_consent(db, user_id)
    s = await _scheme(db, slug)
    if await db.applications.find_one({"user_id": user_id, "scheme_slug": slug, "status": {"$in": list(ACTIVE)}}):
        raise ServiceError("application_exists", 409)
    app = {
        "_id": uuid.uuid4().hex, "user_id": user_id, "scheme_slug": slug, "scheme_version": s["version"],
        "scheme_name": s["name"], "form": form, "documents": await _validate_docs(db, user_id, documents),
        "status": "draft", "history": [{"from": None, "to": "draft", "at": _now(), "by": {"role": "user", "id": user_id},
                                        "note": None}],
        "precheck": None, "assigned_officer_id": None, "officer_remarks": [], "decision": None,
        "created_at": _now(), "updated_at": _now(), "submitted_at": None, "decided_at": None,
    }
    await db.applications.insert_one(app)
    return app


async def own(db: AsyncIOMotorDatabase, user_id: str, app_id: str) -> dict[str, Any]:
    app = await db.applications.find_one({"_id": app_id, "user_id": user_id})
    if not app:
        raise ServiceError("application_not_found", 404)
    return app


async def update_draft(db: AsyncIOMotorDatabase, user_id: str, app_id: str, form: dict[str, Any],
                       documents: list[str]) -> dict[str, Any]:
    app = await own(db, user_id, app_id)
    if app["status"] != "draft":
        raise ServiceError("not_editable", 409)
    await db.applications.update_one({"_id": app_id}, {"$set": {
        "form": form, "documents": await _validate_docs(db, user_id, documents), "updated_at": _now()}})
    return await own(db, user_id, app_id)


async def run_precheck(db: AsyncIOMotorDatabase, user_id: str, app_id: str) -> dict[str, Any]:
    app = await own(db, user_id, app_id)
    report = await precheck.run(db, app)
    if app["status"] == "draft":
        await db.applications.update_one({"_id": app_id}, {"$set": {"precheck": report}})
    return report


async def submit(db: AsyncIOMotorDatabase, user_id: str, app_id: str) -> dict[str, Any]:
    app = await own(db, user_id, app_id)
    if app["status"] != "draft":
        raise ServiceError("invalid_transition", 409, from_status=app["status"], to_status="submitted")
    await transition(db, app_id, "submitted", actor={"role": "user", "id": user_id},
                     extra={"submitted_at": _now()})
    return await _after_submit(db, app_id)


async def _after_submit(db: AsyncIOMotorDatabase, app_id: str) -> dict[str, Any]:
    """System step: run the pre-check, store it, assign an officer, move to precheck_done."""
    app = await db.applications.find_one({"_id": app_id})
    report = await precheck.run(db, app)
    officer = app.get("assigned_officer_id") or await pick_officer(
        db, app["scheme_slug"], (app["form"].get("eligibility") or {}).get("state"))
    await db.applications.update_one({"_id": app_id}, {"$set": {"precheck": report, "assigned_officer_id": officer}})
    moved = await transition(db, app_id, "precheck_done", actor={"role": "system", "id": "precheck"},
                             note=report["suggested"])
    from app.services import verification

    await verification.generate_and_store(db, moved)
    return await db.applications.find_one({"_id": app_id})


async def withdraw(db: AsyncIOMotorDatabase, user_id: str, app_id: str, reason: str | None) -> dict[str, Any]:
    await own(db, user_id, app_id)
    return await transition(db, app_id, "withdrawn", actor={"role": "user", "id": user_id}, note=reason)


async def resubmit(db: AsyncIOMotorDatabase, user_id: str, app_id: str, documents: list[str],
                   note: str | None) -> dict[str, Any]:
    app = await own(db, user_id, app_id)
    if app["status"] != "more_docs_needed":
        raise ServiceError("invalid_transition", 409, from_status=app["status"], to_status="resubmitted")
    ids = await _validate_docs(db, user_id, documents)
    merged = list(dict.fromkeys(app["documents"] + ids))
    app["documents"] = merged
    report = await precheck.run(db, app)
    moved = await transition(db, app_id, "resubmitted", actor={"role": "user", "id": user_id}, note=note,
                             extra={"documents": merged, "precheck": report})
    from app.services import verification

    await verification.generate_and_store(db, moved)
    return await db.applications.find_one({"_id": app_id})


async def list_for_user(db: AsyncIOMotorDatabase, user_id: str) -> list[dict[str, Any]]:
    return [public(a) async for a in db.applications.find({"user_id": user_id}).sort("updated_at", -1)]

# ----------------------------------------------------------------------------- notifications


async def notifications(db: AsyncIOMotorDatabase, user_id: str, limit: int = 30) -> dict[str, Any]:
    rows = [{**{k: v for k, v in n.items() if k not in ("_id", "user_id", "seq")}, "id": n["_id"]}
            async for n in db.notifications.find({"user_id": user_id}).sort("seq", -1).limit(limit)]
    unread = await db.notifications.count_documents({"user_id": user_id, "read": False})
    return {"unread": unread, "items": rows}


async def mark_read(db: AsyncIOMotorDatabase, user_id: str) -> None:
    await db.notifications.update_many({"user_id": user_id, "read": False}, {"$set": {"read": True}})
