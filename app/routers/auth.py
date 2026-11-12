"""/api/auth: register, login (per portal), refresh, logout, me, password change and reset."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings, get_settings
from app.db import get_db
from app.models.user import (
    ChangePasswordIn, LoginIn, RefreshIn, RegisterIn, ResetConfirmIn, ResetRequestIn, TokenPair, UserOut,
)
from app.security import rate_limit
from app.security.deps import AnyUser
from app.services import audit, users
from app.services.errors import ServiceError

router = APIRouter(prefix="/api/auth", tags=["auth"])
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _limit(request: Request, settings: Settings, bucket: str) -> None:
    rate_limit.enforce(request, bucket, settings.rate_limit_auth_per_minute)


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, request: Request, settings: SettingsDep) -> Any:
    _limit(request, settings, "register")
    return users.public_user(await users.register_user(get_db(), body))


@router.post("/login", response_model=TokenPair)
async def login(body: LoginIn, request: Request, settings: SettingsDep) -> Any:
    _limit(request, settings, "login")
    db = get_db()
    user = await users.authenticate(db, settings, body.login_id, body.password, body.portal, body.otp)
    if user["role"] in ("officer", "admin"):
        await audit.record(db, action="auth.login", target={"type": "user", "id": user["_id"]},
                           actor={"id": user["_id"], "role": user["role"], "login_id": user["login_id"],
                                  "ip": rate_limit.client_ip(request)})
    return await users.issue_tokens(db, settings, user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshIn, request: Request, settings: SettingsDep) -> Any:
    _limit(request, settings, "refresh")
    return await users.rotate_refresh(get_db(), settings, body.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshIn, settings: SettingsDep) -> None:
    await users.revoke_refresh(get_db(), settings, body.refresh_token)


@router.get("/me", response_model=UserOut)
async def me(user: AnyUser) -> Any:
    doc = await get_db().users.find_one({"_id": user.id})
    return users.public_user(doc)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: ChangePasswordIn, user: AnyUser, request: Request, settings: SettingsDep) -> None:
    _limit(request, settings, "change-password")
    await users.change_password(get_db(), user.id, body.current_password, body.new_password)
    if user.role in ("officer", "admin"):
        await audit.record(get_db(), actor=user.actor, action="auth.change_password",
                           target={"type": "user", "id": user.id})


@router.post("/password-reset/request")
async def password_reset_request(body: ResetRequestIn, request: Request, settings: SettingsDep) -> dict[str, Any]:
    """Always answers the same way so it cannot be used to discover accounts.

    Prototype: no e-mail/SMS gateway exists. Outside production the token is returned in
    ``dev_reset_token`` so the flow can be demonstrated; in production it is never returned.
    """
    _limit(request, settings, "reset")
    token = await users.request_password_reset(get_db(), settings, body.login_id)
    out: dict[str, Any] = {"message": "If the account exists, reset instructions have been issued."}
    if token and settings.app_env != "prod":
        out["dev_reset_token"] = token
    return out


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def password_reset_confirm(body: ResetConfirmIn, request: Request, settings: SettingsDep) -> None:
    _limit(request, settings, "reset")
    await users.confirm_password_reset(get_db(), body.token, body.new_password)


# ----------------------------------------------------------------------------- optional 2FA (officer/admin)

class OtpIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=6, max_length=10)


@router.post("/2fa/setup")
async def twofa_setup(user: AnyUser) -> dict[str, Any]:
    """Start 2FA: returns a new secret to add to an authenticator app. Not active until confirmed."""
    if user.role not in ("officer", "admin"):
        raise ServiceError("forbidden_role", 403)
    from app.security import totp

    secret = totp.new_secret()
    await get_db().users.update_one({"_id": user.id}, {"$set": {"totp_pending": totp.encrypt(secret)}})
    return {"secret": secret, "otpauth_uri": totp.uri(secret, user.login_id)}


@router.post("/2fa/enable", status_code=status.HTTP_204_NO_CONTENT)
async def twofa_enable(body: OtpIn, user: AnyUser) -> None:
    from app.security import totp

    db = get_db()
    doc = await db.users.find_one({"_id": user.id})
    pending = (doc or {}).get("totp_pending")
    if not pending or not totp.verify(totp.decrypt(pending), body.code):
        raise ServiceError("invalid_otp", 422)
    await db.users.update_one({"_id": user.id}, {"$set": {"totp": {"enabled": True, "secret": pending}},
                                                 "$unset": {"totp_pending": ""}})
    await audit.record(db, actor=user.actor, action="auth.2fa_enabled", target={"type": "user", "id": user.id})


@router.post("/2fa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def twofa_disable(body: OtpIn, user: AnyUser) -> None:
    from app.security import totp

    db = get_db()
    doc = await db.users.find_one({"_id": user.id})
    t = (doc or {}).get("totp") or {}
    if not t.get("enabled") or not totp.verify(totp.decrypt(t["secret"]), body.code):
        raise ServiceError("invalid_otp", 422)
    await db.users.update_one({"_id": user.id}, {"$unset": {"totp": ""}})
    await audit.record(db, actor=user.actor, action="auth.2fa_disabled", target={"type": "user", "id": user.id})
