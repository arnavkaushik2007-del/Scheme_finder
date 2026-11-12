"""Accounts, login with lockout, token issue/rotation/revocation, password changes and officers."""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.config import Settings
from app.security import passwords, tokens
from app.services import audit
from app.services.errors import ServiceError

log = logging.getLogger(__name__)

CONSENT_VERSION = "2026-11-v1"


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def public_user(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc["_id"],
        "login_id": doc["login_id"],
        "full_name": doc.get("full_name", ""),
        "role": doc["role"],
        "status": doc.get("status", "active"),
        "preferred_language": doc.get("preferred_language", "en"),
        "created_at": doc["created_at"],
        "consent": doc.get("consent"),
        "totp_enabled": bool((doc.get("totp") or {}).get("enabled")),
    }


def check_password_policy(password: str, login_id: str) -> None:
    problems = passwords.password_problems(password, login_id)
    if problems:
        raise ServiceError("weak_password", 422, problems=problems)


async def create_account(
    db: AsyncIOMotorDatabase,
    *,
    login_id: str,
    full_name: str,
    password: str,
    role: str,
    preferred_language: str = "en",
    consent: dict[str, Any] | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    check_password_policy(password, login_id)
    doc = {
        "_id": uuid.uuid4().hex,
        "login_id": login_id,
        "full_name": full_name,
        "password_hash": passwords.hash_password(password),
        "role": role,
        "status": "active",
        "preferred_language": preferred_language,
        "consent": consent,
        "failed_logins": 0,
        "locked_until": None,
        "created_at": _now(),
        "created_by": created_by,
        "deleted_at": None,
    }
    try:
        await db.users.insert_one(doc)
    except DuplicateKeyError as exc:
        raise ServiceError("login_id_taken", 409) from exc
    return doc


async def register_user(db: AsyncIOMotorDatabase, data: Any) -> dict[str, Any]:
    if not data.consent_accepted:
        raise ServiceError("consent_required", 422)
    consent = {"accepted_at": _now(), "mode": data.consent_mode, "version": CONSENT_VERSION}
    return await create_account(
        db, login_id=data.login_id, full_name=data.full_name, password=data.password, role="user",
        preferred_language=data.preferred_language, consent=consent,
    )


async def authenticate(
    db: AsyncIOMotorDatabase, settings: Settings, login_id: str, password: str, portal: str,
    otp: str | None = None,
) -> dict[str, Any]:
    """Check credentials for the given portal. Wrong portal looks exactly like a wrong password.
    Officers/admins who turned on two-factor login must also give a valid authenticator code."""
    user = await db.users.find_one({"login_id": login_id, "deleted_at": None})
    if user is None:
        passwords.verify_password(passwords.DUMMY_HASH, password)  # equalise timing
        raise ServiceError("invalid_credentials", 401)

    locked_until = _aware(user.get("locked_until"))
    if locked_until and locked_until > _now():
        minutes = max(1, int((locked_until - _now()).total_seconds() // 60) + 1)
        raise ServiceError("account_locked", 423, retry_after_minutes=minutes)

    ok = passwords.verify_password(user["password_hash"], password) and user["role"] == portal
    if ok and (user.get("totp") or {}).get("enabled"):
        from app.security import totp

        if not otp:
            raise ServiceError("otp_required", 401)
        ok = totp.verify(totp.decrypt(user["totp"]["secret"]), otp)
    if not ok:
        failures = int(user.get("failed_logins", 0)) + 1
        update: dict[str, Any] = {"failed_logins": failures}
        if failures >= settings.login_max_failures:
            update = {"failed_logins": 0, "locked_until": _now() + timedelta(minutes=settings.lockout_minutes)}
            log.warning("account locked after repeated failures (user_id=%s)", user["_id"])
            if user["role"] in ("officer", "admin"):
                await audit.record(db, actor=audit.system_actor(), action="auth.lockout",
                                   target={"type": "user", "id": user["_id"]})
        await db.users.update_one({"_id": user["_id"]}, {"$set": update})
        if "locked_until" in update:
            raise ServiceError("account_locked", 423, retry_after_minutes=settings.lockout_minutes)
        raise ServiceError("invalid_credentials", 401, attempts_left=settings.login_max_failures - failures)

    if user.get("status") != "active":
        raise ServiceError("account_suspended", 403)

    update = {"failed_logins": 0, "locked_until": None, "last_login_at": _now()}
    if passwords.needs_rehash(user["password_hash"]):
        update["password_hash"] = passwords.hash_password(password)
    await db.users.update_one({"_id": user["_id"]}, {"$set": update})
    return user


async def issue_tokens(db: AsyncIOMotorDatabase, settings: Settings, user: dict[str, Any]) -> dict[str, Any]:
    access = tokens.create_access_token(settings, user["_id"], user["role"])
    refresh, jti, exp = tokens.create_refresh_token(settings, user["_id"], user["role"])
    await db.refresh_tokens.insert_one(
        {"_id": uuid.uuid4().hex, "jti": jti, "user_id": user["_id"], "expires_at": exp, "revoked": False,
         "created_at": _now()}
    )
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "role": user["role"],
            "expires_in": settings.access_token_minutes * 60}


async def rotate_refresh(db: AsyncIOMotorDatabase, settings: Settings, refresh_token: str) -> dict[str, Any]:
    try:
        claims = tokens.decode_token(settings, refresh_token, "refresh")
    except tokens.TokenError as exc:
        raise ServiceError("invalid_refresh_token", 401) from exc
    stored = await db.refresh_tokens.find_one({"jti": claims.jti})
    if stored is None:
        raise ServiceError("invalid_refresh_token", 401)
    if stored["revoked"]:
        # A revoked token being replayed suggests theft: revoke every session of that user.
        await db.refresh_tokens.update_many({"user_id": stored["user_id"]}, {"$set": {"revoked": True}})
        log.warning("refresh token reuse detected; all sessions revoked (user_id=%s)", stored["user_id"])
        raise ServiceError("invalid_refresh_token", 401)
    user = await db.users.find_one({"_id": claims.sub, "deleted_at": None})
    if not user or user.get("status") != "active" or user["role"] != claims.role:
        raise ServiceError("invalid_refresh_token", 401)
    await db.refresh_tokens.update_one({"_id": stored["_id"]}, {"$set": {"revoked": True, "rotated_at": _now()}})
    return await issue_tokens(db, settings, user)


async def revoke_refresh(db: AsyncIOMotorDatabase, settings: Settings, refresh_token: str) -> None:
    try:
        claims = tokens.decode_token(settings, refresh_token, "refresh")
    except tokens.TokenError:
        return
    await db.refresh_tokens.update_one({"jti": claims.jti}, {"$set": {"revoked": True}})


async def change_password(db: AsyncIOMotorDatabase, user_id: str, current: str, new: str) -> None:
    user = await db.users.find_one({"_id": user_id})
    if not user or not passwords.verify_password(user["password_hash"], current):
        raise ServiceError("invalid_credentials", 401)
    check_password_policy(new, user["login_id"])
    await db.users.update_one({"_id": user_id}, {"$set": {"password_hash": passwords.hash_password(new),
                                                          "password_changed_at": _now()}})
    await db.refresh_tokens.update_many({"user_id": user_id}, {"$set": {"revoked": True}})


async def request_password_reset(db: AsyncIOMotorDatabase, settings: Settings, login_id: str) -> str | None:
    """Create a single-use reset token. Returns it (the caller decides how to deliver it).

    Prototype: there is no e-mail/SMS gateway, so in dev the token is shown on the server console.
    Officers and admins cannot self-reset; the admin resets officer passwords.
    """
    user = await db.users.find_one({"login_id": login_id, "deleted_at": None, "role": "user"})
    if not user:
        return None
    token = secrets.token_urlsafe(32)
    await db.password_resets.insert_one({
        "_id": uuid.uuid4().hex, "token_hash": tokens.sha256(token), "user_id": user["_id"],
        "expires_at": _now() + timedelta(minutes=settings.password_reset_minutes), "used": False,
    })
    return token


async def confirm_password_reset(db: AsyncIOMotorDatabase, token: str, new_password: str) -> None:
    rec = await db.password_resets.find_one({"token_hash": tokens.sha256(token)})
    if not rec or rec["used"] or _aware(rec["expires_at"]) < _now():
        raise ServiceError("invalid_reset_token", 400)
    user = await db.users.find_one({"_id": rec["user_id"]})
    if not user:
        raise ServiceError("invalid_reset_token", 400)
    check_password_policy(new_password, user["login_id"])
    await db.password_resets.update_one({"_id": rec["_id"]}, {"$set": {"used": True}})
    await db.users.update_one({"_id": user["_id"]}, {"$set": {
        "password_hash": passwords.hash_password(new_password), "failed_logins": 0, "locked_until": None,
        "password_changed_at": _now()}})
    await db.refresh_tokens.update_many({"user_id": user["_id"]}, {"$set": {"revoked": True}})


# ---------------------------------------------------------------- officers (admin only)

def _officer_out(user: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    profile = profile or {}
    return {
        "id": user["_id"], "login_id": user["login_id"], "full_name": user.get("full_name", ""),
        "status": user.get("status", "active"), "region": profile.get("region", ""),
        "assigned_schemes": profile.get("assigned_schemes", []), "created_at": user["created_at"],
        "created_by": user.get("created_by"),
    }


async def create_officer(db: AsyncIOMotorDatabase, data: Any, actor: dict[str, Any]) -> dict[str, Any]:
    user = await create_account(db, login_id=data.login_id, full_name=data.full_name, password=data.password,
                                role="officer", created_by=actor["id"])
    profile = {"_id": uuid.uuid4().hex, "user_id": user["_id"], "region": data.region,
               "assigned_schemes": data.assigned_schemes, "created_by": actor["id"], "created_at": _now()}
    await db.officers.insert_one(profile)
    out = _officer_out(user, profile)
    await audit.record(db, actor=actor, action="officer.create", target={"type": "officer", "id": user["_id"]},
                       after={k: v for k, v in out.items() if k != "created_at"})
    return out


async def list_officers(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    out = []
    async for user in db.users.find({"role": "officer", "deleted_at": None}).sort("created_at", 1):
        profile = await db.officers.find_one({"user_id": user["_id"]})
        out.append(_officer_out(user, profile))
    return out


async def set_officer_status(db: AsyncIOMotorDatabase, officer_id: str, status: str, reason: str,
                             actor: dict[str, Any]) -> dict[str, Any]:
    user = await db.users.find_one({"_id": officer_id, "role": "officer", "deleted_at": None})
    if not user:
        raise ServiceError("officer_not_found", 404)
    before = user.get("status")
    await db.users.update_one({"_id": officer_id}, {"$set": {"status": status}})
    if status != "active":
        await db.refresh_tokens.update_many({"user_id": officer_id}, {"$set": {"revoked": True}})
    await audit.record(db, actor=actor, action="officer.status", target={"type": "officer", "id": officer_id},
                       before={"status": before}, after={"status": status}, reason=reason)
    user["status"] = status
    return _officer_out(user, await db.officers.find_one({"user_id": officer_id}))
