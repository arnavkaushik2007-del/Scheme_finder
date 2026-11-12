"""JWT access and refresh tokens. The role is carried inside the token, but every request
still re-reads the user from the database so suspensions and role changes apply at once."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt

from app.config import Settings

TokenType = Literal["access", "refresh"]


@dataclass(frozen=True)
class TokenClaims:
    sub: str
    role: str
    jti: str
    type: TokenType
    exp: datetime


class TokenError(Exception):
    pass


def _encode(settings: Settings, sub: str, role: str, ttype: TokenType, ttl: timedelta) -> tuple[str, str, datetime]:
    now = datetime.now(UTC)
    jti = uuid.uuid4().hex
    exp = now + ttl
    payload = {"sub": sub, "role": role, "type": ttype, "jti": jti, "iat": now, "exp": exp, "iss": "scheme-finder"}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), jti, exp


def create_access_token(settings: Settings, user_id: str, role: str) -> str:
    token, _, _ = _encode(settings, user_id, role, "access", timedelta(minutes=settings.access_token_minutes))
    return token


def create_refresh_token(settings: Settings, user_id: str, role: str) -> tuple[str, str, datetime]:
    return _encode(settings, user_id, role, "refresh", timedelta(days=settings.refresh_token_days))


def decode_token(settings: Settings, token: str, expected: TokenType) -> TokenClaims:
    try:
        data = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer="scheme-finder",
            options={"require": ["exp", "sub", "type", "jti", "role"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("invalid") from exc
    if data.get("type") != expected:
        raise TokenError("wrong_type")
    return TokenClaims(
        sub=data["sub"], role=data["role"], jti=data["jti"], type=data["type"],
        exp=datetime.fromtimestamp(data["exp"], UTC),
    )


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
