"""Admin console: officers, citizens, masked database browser with logged reveals, analytics.

"Full power" still means masked by default: personal fields are masked in every listing, and
revealing a record needs a written reason that goes to the audit log. Secrets (password hashes,
token hashes, file keys, 2FA secrets) are never returned, even on reveal.
"""

from __future__ import annotations

import re
import secrets
import string
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.security import passwords
from app.services import audit, profiles, users
from app.services.errors import ServiceError


def _now() -> datetime:
    return datetime.now(UTC)

# ----------------------------------------------------------------------------- officers


async def update_officer(db: AsyncIOMotorDatabase, officer_id: str, data: dict[str, Any],
                         actor: dict[str, Any]) -> dict[str, Any]:
    user = await db.users.find_one({"_id": officer_id, "role": "officer", "deleted_at": None})
    if not user:
        raise ServiceError("officer_not_found", 404)
    prof = await db.officers.find_one({"user_id": officer_id}) or {}
    if data.get("assigned_schemes"):
        known = {s["slug"] async for s in db.schemes.find({"slug": {"$in": data["assigned_schemes"]}}, {"slug": 1})}
        if unknown := sorted(set(data["assigned_schemes"]) - known):
            raise ServiceError("unknown_schemes", 422, schemes=unknown)
    before = {"full_name": user.get("full_name"), "region": prof.get("region", ""),
              "assigned_schemes": prof.get("assigned_schemes", [])}
    if "full_name" in data:
        await db.users.update_one({"_id": officer_id}, {"$set": {"full_name": data["full_name"]}})
    prof_update = {k: data[k] for k in ("region", "assigned_schemes") if k in data}
    if prof_update:
        await db.officers.update_one({"user_id": officer_id}, {"$set": prof_update,
                                                              "$setOnInsert": {"created_at": _now()}}, upsert=True)
    after = {**before, **data}
    await audit.record(db, actor=actor, action="officer.update", target={"type": "officer", "id": officer_id},
                       before=before, after=after, reason=data.get("reason"))
    return next(o for o in await users.list_officers(db) if o["id"] == officer_id)


def _temp_password() -> str:
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(10))
    return f"{core}#{secrets.randbelow(90) + 10}Aa"


async def reset_officer_password(db: AsyncIOMotorDatabase, officer_id: str, reason: str,
                                 actor: dict[str, Any]) -> str:
    user = await db.users.find_one({"_id": officer_id, "role": "officer", "deleted_at": None})
    if not user:
        raise ServiceError("officer_not_found", 404)
    temp = _temp_password()
    await db.users.update_one({"_id": officer_id}, {"$set": {
        "password_hash": passwords.hash_password(temp), "must_change_password": True,
        "failed_logins": 0, "locked_until": None}, "$unset": {"totp": ""}})
    await db.refresh_tokens.update_many({"user_id": officer_id}, {"$set": {"revoked": True}})
    await audit.record(db, actor=actor, action="officer.reset_password", target={"type": "officer", "id": officer_id},
                       reason=reason, after={"two_factor_reset": True})
    return temp


async def delete_officer(db: AsyncIOMotorDatabase, officer_id: str, reason: str, actor: dict[str, Any]) -> int:
    """Soft delete; their open cases go back to the unassigned pool."""
    user = await db.users.find_one({"_id": officer_id, "role": "officer", "deleted_at": None})
    if not user:
        raise ServiceError("officer_not_found", 404)
    await db.users.update_one({"_id": officer_id}, {"$set": {"deleted_at": _now(), "status": "suspended"}})
    await db.refresh_tokens.update_many({"user_id": officer_id}, {"$set": {"revoked": True}})
    from app.services.officer import OPEN

    res = await db.applications.update_many({"assigned_officer_id": officer_id, "status": {"$in": list(OPEN)}},
                                            {"$set": {"assigned_officer_id": None}})
    await audit.record(db, actor=actor, action="officer.delete", target={"type": "officer", "id": officer_id},
                       before={"login_id": user["login_id"]}, after={"cases_unassigned": res.modified_count},
                       reason=reason)
    return res.modified_count

# ----------------------------------------------------------------------------- citizens


def mask_login(login_id: str) -> str:
    if "@" in login_id:
        name, dom = login_id.split("@", 1)
        return (name[:2] + "•" * max(1, len(name) - 2)) + "@" + dom
    if login_id.isdigit():
        return "•" * (len(login_id) - 4) + login_id[-4:]
    return login_id[:2] + "•" * max(1, len(login_id) - 2)


def mask_name(name: str) -> str:
    return " ".join(p[:1] + "•" * max(1, len(p) - 1) for p in (name or "").split()) or "—"


async def list_users(db: AsyncIOMotorDatabase, q: str | None, status: str | None, flagged_only: bool,
                     limit: int = 100) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"role": "user", "deleted_at": None}
    if status:
        query["status"] = status
    flags = {f["user_id"]: f async for f in db.fraud_flags.find({"cleared": False})}
    out = []
    async for u in db.users.find(query).sort("created_at", -1).limit(1000):
        if q and q.lower() not in u["login_id"].lower() and q.lower() not in (u.get("full_name") or "").lower() \
                and q != u["_id"]:
            continue
        if flagged_only and u["_id"] not in flags:
            continue
        f = flags.get(u["_id"])
        out.append({
            "id": u["_id"], "login_id": mask_login(u["login_id"]), "full_name": mask_name(u.get("full_name", "")),
            "status": u.get("status"), "created_at": u["created_at"], "last_login_at": u.get("last_login_at"),
            "applications": await db.applications.count_documents({"user_id": u["_id"]}),
            "documents": await db.documents.count_documents({"user_id": u["_id"], "deleted_at": None}),
            "flag": {"score": f["score"], "reasons": f["reasons"], "anomaly": f.get("anomaly")} if f else None,
        })
        if len(out) >= limit:
            break
    return out


async def user_detail(db: AsyncIOMotorDatabase, user_id: str) -> dict[str, Any]:
    u = await db.users.find_one({"_id": user_id, "role": "user"})
    if not u:
        raise ServiceError("user_not_found", 404)
    apps = [{"id": a["_id"], "scheme_name": a["scheme_name"], "status": a["status"], "updated_at": a["updated_at"]}
            async for a in db.applications.find({"user_id": user_id}).sort("updated_at", -1)]
    flag = await db.fraud_flags.find_one({"user_id": user_id})
    return {"id": u["_id"], "login_id": mask_login(u["login_id"]), "full_name": mask_name(u.get("full_name", "")),
            "status": u.get("status"), "deleted": u.get("deleted_at") is not None, "created_at": u["created_at"],
            "consent_mode": (u.get("consent") or {}).get("mode"), "applications": apps,
            "flag": {k: v for k, v in flag.items() if k != "_id"} if flag else None}


async def set_user_status(db: AsyncIOMotorDatabase, user_id: str, status: str, reason: str,
                          actor: dict[str, Any]) -> dict[str, Any]:
    u = await db.users.find_one({"_id": user_id, "role": "user", "deleted_at": None})
    if not u:
        raise ServiceError("user_not_found", 404)
    await db.users.update_one({"_id": user_id}, {"$set": {"status": status}})
    if status != "active":
        await db.refresh_tokens.update_many({"user_id": user_id}, {"$set": {"revoked": True}})
    await audit.record(db, actor=actor, action=f"user.{'suspend' if status != 'active' else 'activate'}",
                       target={"type": "user", "id": user_id}, before={"status": u.get("status")},
                       after={"status": status}, reason=reason)
    return await user_detail(db, user_id)


async def delete_user(db: AsyncIOMotorDatabase, user_id: str, reason: str, actor: dict[str, Any]) -> None:
    """Remove a fake account: purge stored personal data, withdraw open applications, soft-delete."""
    u = await db.users.find_one({"_id": user_id, "role": "user", "deleted_at": None})
    if not u:
        raise ServiceError("user_not_found", 404)
    files = await profiles.purge_personal_data(db, user_id)
    from app.services.applications import ACTIVE

    open_apps = await db.applications.update_many({"user_id": user_id, "status": {"$in": list(ACTIVE)}},
                                                  {"$set": {"status": "withdrawn", "updated_at": _now()},
                                                   "$push": {"history": {"from": None, "to": "withdrawn", "at": _now(),
                                                                         "by": {"role": "admin", "id": actor["id"]},
                                                                         "note": "Account removed by admin"}}})
    await db.users.update_one({"_id": user_id}, {"$set": {
        "deleted_at": _now(), "status": "suspended", "login_id": f"deleted-{user_id}", "full_name": "",
        "password_hash": "!", "consent": None}})
    await db.refresh_tokens.update_many({"user_id": user_id}, {"$set": {"revoked": True}})
    await db.fraud_flags.update_many({"user_id": user_id}, {"$set": {"cleared": True, "resolution": "deleted"}})
    await audit.record(db, actor=actor, action="user.delete", target={"type": "user", "id": user_id},
                       before={"login_id": mask_login(u["login_id"])},
                       after={"documents_purged": files, "applications_withdrawn": open_apps.modified_count},
                       reason=reason)

# ----------------------------------------------------------------------------- database browser

COLLECTIONS = ("users", "officers", "profiles", "applications", "documents", "decisions", "ai_reports",
               "schemes", "scheme_versions", "rule_proposals", "required_documents", "notifications", "chat_logs",
               "fraud_flags")
NEVER = {"password_hash", "token_hash", "storage_key", "totp", "totp_pending", "secret", "jti"}
SENSITIVE = {"login_id", "full_name", "date_of_birth", "address", "district", "aadhaar_last4", "bank_account_last4",
             "filename", "question", "answer", "note", "comment"}


def _mask_value(key: str, v: Any) -> Any:
    if v is None or v == "":
        return v
    if key == "login_id" and isinstance(v, str):
        return mask_login(v)
    if key == "full_name" and isinstance(v, str):
        return mask_name(v)
    if key in ("aadhaar_last4", "bank_account_last4"):
        return "••••"
    return "•••• (masked)"


def scrub(obj: Any, *, reveal: bool, key: str = "") -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in NEVER:
                out[k] = "[secret — never shown]"
            elif k in ("personal", "confirmed_fields") and not reveal:
                out[k] = {kk: _mask_value(kk, vv) for kk, vv in (v or {}).items()} if isinstance(v, dict) else v
            elif k == "fields" and isinstance(v, dict) and not reveal:
                out[k] = {kk: {**vv, "value": _mask_value(kk if kk in SENSITIVE else "x", vv.get("value"))}
                          if isinstance(vv, dict) and kk in SENSITIVE else vv for kk, vv in v.items()}
            else:
                out[k] = scrub(v, reveal=reveal, key=k)
        return out
    if isinstance(obj, list):
        return [scrub(x, reveal=reveal, key=key) for x in obj]
    if not reveal and key in SENSITIVE:
        return _mask_value(key, obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


async def browse(db: AsyncIOMotorDatabase, collection: str, *, q: str | None, skip: int, limit: int) -> dict[str, Any]:
    if collection not in COLLECTIONS:
        raise ServiceError("unknown_collection", 404)
    query: dict[str, Any] = {}
    if q:
        query = {"_id": q} if re.fullmatch(r"[0-9a-f]{32}", q) else {"$or": [
            {"slug": q}, {"scheme_slug": q}, {"user_id": q}, {"application_id": q}, {"status": q}, {"doc_type": q},
            {"role": q}]}
    coll = db[collection]
    total = await coll.count_documents(query)
    rows = [scrub(d, reveal=False) async for d in coll.find(query).sort("_id", 1).skip(skip).limit(limit)]
    return {"collection": collection, "total": total, "skip": skip, "rows": rows,
            "masked": True, "note": "Personal fields are masked. Revealing a record requires a reason and is logged."}


async def reveal(db: AsyncIOMotorDatabase, collection: str, doc_id: str, reason: str,
                 actor: dict[str, Any]) -> dict[str, Any]:
    if collection not in COLLECTIONS:
        raise ServiceError("unknown_collection", 404)
    doc = await db[collection].find_one({"_id": doc_id})
    if not doc:
        raise ServiceError("not_found", 404)
    await audit.record(db, actor=actor, action="data.reveal", target={"type": collection, "id": doc_id}, reason=reason)
    return scrub(doc, reveal=True)

# ----------------------------------------------------------------------------- analytics


async def analytics(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    by_scheme: dict[str, dict[str, Any]] = {}
    async for a in db.applications.find({}, {"scheme_slug": 1, "scheme_name": 1, "status": 1}):
        row = by_scheme.setdefault(a["scheme_slug"], {"slug": a["scheme_slug"], "name": a["scheme_name"], "total": 0,
                                                       "approved": 0, "rejected": 0, "open": 0, "withdrawn": 0})
        row["total"] += 1
        st = a["status"]
        if st in ("approved", "rejected", "withdrawn"):
            row[st] += 1
        elif st == "closed":
            pass
        elif st != "draft":
            row["open"] += 1
    decisions = [d async for d in db.decisions.find()]
    finals = [d for d in decisions if d["action"] in ("approve", "reject")]
    for d in finals:  # closed cases: count by their final decision
        row = by_scheme.get(d["scheme_slug"])
        if row is not None:
            app = await db.applications.find_one({"_id": d["application_id"]}, {"status": 1})
            if app and app["status"] == "closed":
                row["approved" if d["action"] == "approve" else "rejected"] += 1
    for row in by_scheme.values():
        decided = row["approved"] + row["rejected"]
        row["approval_rate"] = round(row["approved"] / decided, 3) if decided else None

    with_ai = [d for d in decisions if d.get("ai_suggested")]
    final_ai = [d for d in finals if d.get("ai_suggested") in ("eligible", "not_eligible")]
    officers: dict[str, dict[str, Any]] = {}
    for d in decisions:
        o = officers.setdefault(d["officer_id"], {"officer_id": d["officer_id"], "decisions": 0, "overrides": 0})
        o["decisions"] += 1
        o["overrides"] += int(bool(d.get("differs_from_ai")))
    for o in officers.values():
        u = await db.users.find_one({"_id": o["officer_id"]}, {"full_name": 1})
        o["name"] = (u or {}).get("full_name", "—")
        o["override_rate"] = round(o["overrides"] / o["decisions"], 3) if o["decisions"] else None
    chats = await db.chat_logs.count_documents({})
    abstained = await db.chat_logs.count_documents({"abstained": True})
    down = await db.chat_logs.count_documents({"feedback.rating": "down"})
    up = await db.chat_logs.count_documents({"feedback.rating": "up"})
    return {
        "applications_by_scheme": sorted(by_scheme.values(), key=lambda r: -r["total"]),
        "status_counts": {s: await db.applications.count_documents({"status": s}) for s in
                          ("draft", "precheck_done", "under_review", "more_docs_needed", "resubmitted", "approved",
                           "rejected", "closed", "withdrawn")},
        "ai": {
            "decisions_with_ai": len(with_ai),
            "agreement_rate": round(sum(not d["differs_from_ai"] for d in with_ai) / len(with_ai), 3) if with_ai else None,
            # "accuracy" of a definite AI suggestion = officer's final decision went the same way
            "definite_suggestion_accuracy": round(sum(not d["differs_from_ai"] for d in final_ai) / len(final_ai), 3)
            if final_ai else None,
            "override_rate": round(sum(d["differs_from_ai"] for d in with_ai) / len(with_ai), 3) if with_ai else None,
        },
        "officers": sorted(officers.values(), key=lambda o: -o["decisions"]),
        "assistant": {"questions": chats, "abstained": abstained, "thumbs_up": up, "thumbs_down": down,
                      "abstention_rate": round(abstained / chats, 3) if chats else None},
        "users": {"citizens": await db.users.count_documents({"role": "user", "deleted_at": None}),
                  "flagged": await db.fraud_flags.count_documents({"cleared": False})},
    }
