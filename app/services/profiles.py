"""Citizen profile, consent, saved schemes, document checklist, and "delete my data"."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.profile import PersonalInfo
from app.models.scheme import EligibilityProfile
from app.security import passwords
from app.services import audit, storage
from app.services.errors import ServiceError
from app.services.users import CONSENT_VERSION

log = logging.getLogger(__name__)

# Fields that matter for most schemes; used for the "profile completeness" meter.
CORE_FIELDS = ["state", "residence", "age", "gender", "category", "annual_income", "occupation", "family_size",
               "marital_status", "is_bpl", "has_bank_account", "is_income_tax_payer", "education_level",
               "disability_percent"]


def _now() -> datetime:
    return datetime.now(UTC)


def completeness(eligibility: dict[str, Any]) -> int:
    filled = sum(1 for f in CORE_FIELDS if eligibility.get(f) is not None)
    return round(100 * filled / len(CORE_FIELDS))


async def consent_of(db: AsyncIOMotorDatabase, user_id: str) -> dict[str, Any]:
    user = await db.users.find_one({"_id": user_id}, {"consent": 1})
    return (user or {}).get("consent") or {}


async def require_save_consent(db: AsyncIOMotorDatabase, user_id: str) -> None:
    c = await consent_of(db, user_id)
    if c.get("mode") != "save_profile" or c.get("withdrawn_at"):
        raise ServiceError("consent_mode_use_once", 409)


async def get_profile(db: AsyncIOMotorDatabase, user_id: str) -> dict[str, Any]:
    doc = await db.profiles.find_one({"user_id": user_id}) or {}
    elig = doc.get("eligibility", {})
    return {
        "eligibility": elig,
        "personal": doc.get("personal", {}),
        "sources": doc.get("sources", {}),
        "saved_schemes": doc.get("saved_schemes", []),
        "completeness": completeness(elig),
        "updated_at": doc.get("updated_at"),
        "consent": await consent_of(db, user_id),
    }


async def save_profile(db: AsyncIOMotorDatabase, user_id: str, eligibility: dict[str, Any],
                       personal: dict[str, Any]) -> dict[str, Any]:
    await require_save_consent(db, user_id)
    current = await db.profiles.find_one({"user_id": user_id}) or {}
    sources = dict(current.get("sources", {}))
    for k, v in {**eligibility, **personal}.items():
        old = current.get("eligibility", {}).get(k, current.get("personal", {}).get(k))
        if v != old:
            sources[k] = "manual"
    for k in list(sources):
        if k not in eligibility and k not in personal:
            sources.pop(k)
    await db.profiles.update_one(
        {"user_id": user_id},
        {"$set": {"eligibility": eligibility, "personal": personal, "sources": sources, "updated_at": _now()},
         "$setOnInsert": {"saved_schemes": [], "created_at": _now()}},
        upsert=True)
    return await get_profile(db, user_id)


async def merge_fields(db: AsyncIOMotorDatabase, user_id: str, fields: dict[str, Any], source: str) -> dict[str, Any]:
    """Apply confirmed document fields. Values are validated through the same models as manual edits."""
    await require_save_consent(db, user_id)
    prof = await get_profile(db, user_id)
    elig, pers = dict(prof["eligibility"]), dict(prof["personal"])
    elig_keys, pers_keys = set(EligibilityProfile.model_fields), set(PersonalInfo.model_fields)
    for k, v in fields.items():
        if k in elig_keys:
            elig[k] = v
        elif k in pers_keys:
            pers[k] = v
    try:
        elig = EligibilityProfile.model_validate(elig).model_dump(exclude_none=True)
        pers = PersonalInfo.model_validate(pers).model_dump(exclude_none=True)
    except ValueError as exc:
        raise ServiceError("invalid_fields", 422, message=str(exc)[:300]) from exc
    sources = dict(prof["sources"])
    for k in fields:
        if k in elig_keys or k in pers_keys:
            sources[k] = source
    await db.profiles.update_one(
        {"user_id": user_id},
        {"$set": {"eligibility": elig, "personal": pers, "sources": sources, "updated_at": _now()},
         "$setOnInsert": {"saved_schemes": [], "created_at": _now()}},
        upsert=True)
    return await get_profile(db, user_id)


async def clear_profile(db: AsyncIOMotorDatabase, user_id: str) -> None:
    await db.profiles.delete_one({"user_id": user_id})


async def set_consent(db: AsyncIOMotorDatabase, user_id: str, mode: str, accepted: bool) -> dict[str, Any]:
    if not accepted:
        raise ServiceError("consent_required", 422)
    consent = {"accepted_at": _now(), "mode": mode, "version": CONSENT_VERSION, "withdrawn_at": None}
    await db.users.update_one({"_id": user_id}, {"$set": {"consent": consent}})
    if mode == "use_once":
        await purge_personal_data(db, user_id)
    return consent


async def withdraw_consent(db: AsyncIOMotorDatabase, user_id: str) -> dict[str, Any]:
    """Withdrawing consent deletes everything stored on the strength of that consent."""
    consent = {"accepted_at": None, "mode": "use_once", "version": CONSENT_VERSION, "withdrawn_at": _now()}
    await db.users.update_one({"_id": user_id}, {"$set": {"consent": consent}})
    await purge_personal_data(db, user_id)
    return consent


async def purge_personal_data(db: AsyncIOMotorDatabase, user_id: str) -> int:
    """Delete the profile and every stored document (encrypted files removed from disk)."""
    removed = 0
    async for d in db.documents.find({"user_id": user_id, "deleted_at": None}):
        storage.delete(d.get("storage_key"))
        await db.documents.update_one({"_id": d["_id"]}, {"$set": {
            "deleted_at": _now(), "storage_key": None, "extracted": None, "confirmed_fields": None,
            "filename": None, "purged": True}})
        removed += 1
    await clear_profile(db, user_id)
    return removed


async def toggle_saved(db: AsyncIOMotorDatabase, user_id: str, slug: str, save: bool) -> list[str]:
    await require_save_consent(db, user_id)
    if save and not await db.schemes.find_one({"slug": slug, "deleted_at": None}, {"_id": 1}):
        raise ServiceError("scheme_not_found", 404)
    op = {"$addToSet": {"saved_schemes": slug}} if save else {"$pull": {"saved_schemes": slug}}
    await db.profiles.update_one({"user_id": user_id}, {**op, "$set": {"updated_at": _now()}}, upsert=True)
    return (await db.profiles.find_one({"user_id": user_id}) or {}).get("saved_schemes", [])


async def checklist(db: AsyncIOMotorDatabase, user_id: str) -> list[dict[str, Any]]:
    """For each saved scheme: required documents and whether the user has uploaded one of that type."""
    prof = await db.profiles.find_one({"user_id": user_id}) or {}
    have = {d["doc_type"] async for d in db.documents.find({"user_id": user_id, "deleted_at": None},
                                                            {"doc_type": 1})}
    catalog = {d["_id"]: d async for d in db.required_documents.find()}
    out = []
    for slug in prof.get("saved_schemes", []):
        s = await db.schemes.find_one({"slug": slug, "deleted_at": None}, {"slug": 1, "name": 1, "documents": 1})
        if not s:
            continue
        docs = [{"id": d, "name": catalog.get(d, {}).get("name", {"en": d, "hi": d}),
                 "have": d in have or (d == "bpl_card" and "ration_card" in have)} for d in s.get("documents", [])]
        out.append({"slug": slug, "name": s["name"], "documents": docs,
                    "done": sum(1 for d in docs if d["have"]), "total": len(docs)})
    return out


async def delete_account(db: AsyncIOMotorDatabase, user_id: str, password: str, ip: str | None) -> None:
    user = await db.users.find_one({"_id": user_id, "deleted_at": None})
    if not user or not passwords.verify_password(user["password_hash"], password):
        raise ServiceError("invalid_credentials", 401)
    files = await purge_personal_data(db, user_id)
    # Soft delete with the personal fields removed; the row remains only as a tombstone so the
    # audit trail of any past application stays consistent.
    await db.users.update_one({"_id": user_id}, {"$set": {
        "deleted_at": _now(), "status": "suspended", "login_id": f"deleted-{user_id}", "full_name": "",
        "password_hash": "!", "consent": None}})
    await db.refresh_tokens.update_many({"user_id": user_id}, {"$set": {"revoked": True}})
    await audit.record(db, actor={"id": user_id, "role": "user", "login_id": "self", "ip": ip},
                       action="user.self_delete", target={"type": "user", "id": user_id},
                       after={"documents_purged": files})
    log.info("account deleted on request (user_id=%s)", user_id)
