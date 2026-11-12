"""FastAPI dependencies for authentication and server-side role checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.db import get_db
from app.security.rate_limit import client_ip
from app.security.tokens import TokenError, decode_token

_bearer = HTTPBearer(auto_error=False)

ROLES = ("user", "officer", "admin")


@dataclass(frozen=True)
class CurrentUser:
    id: str
    role: str
    login_id: str
    full_name: str
    ip: str

    @property
    def actor(self) -> dict[str, Any]:
        return {"id": self.id, "role": self.role, "login_id": self.login_id, "ip": self.ip}


def _unauthorized(code: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": code}, headers={"WWW-Authenticate": "Bearer"})


async def get_current_user(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CurrentUser:
    if creds is None or creds.scheme.lower() != "bearer":
        raise _unauthorized("not_authenticated")
    try:
        claims = decode_token(settings, creds.credentials, "access")
    except TokenError as exc:
        raise _unauthorized(f"token_{exc}") from exc
    user = await get_db().users.find_one({"_id": claims.sub})
    if not user or user.get("deleted_at") is not None:
        raise _unauthorized("account_not_found")
    if user.get("status") != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "account_suspended"})
    if user["role"] != claims.role:  # role changed since the token was issued
        raise _unauthorized("role_changed")
    return CurrentUser(id=user["_id"], role=user["role"], login_id=user["login_id"],
                       full_name=user.get("full_name", ""), ip=client_ip(request))


def require_roles(*roles: str) -> Callable[..., Any]:
    unknown = set(roles) - set(ROLES)
    if unknown:
        raise ValueError(f"unknown roles {unknown}")

    async def _check(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden_role", "required": list(roles)})
        return user

    return _check


AnyUser = Annotated[CurrentUser, Depends(get_current_user)]
UserOnly = Annotated[CurrentUser, Depends(require_roles("user"))]
OfficerOnly = Annotated[CurrentUser, Depends(require_roles("officer"))]
AdminOnly = Annotated[CurrentUser, Depends(require_roles("admin"))]
StaffOnly = Annotated[CurrentUser, Depends(require_roles("officer", "admin"))]
