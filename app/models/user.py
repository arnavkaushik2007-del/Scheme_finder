"""Pydantic models for accounts and authentication."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal["user", "officer", "admin"]
Portal = Literal["user", "officer", "admin"]
AccountStatus = Literal["active", "suspended"]

_LOGIN_RE = re.compile(r"^(?:[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|[6-9]\d{9}|[a-z][a-z0-9._-]{3,31})$")


def _norm_login(v: str) -> str:
    v = v.strip().lower()
    if not _LOGIN_RE.match(v):
        raise ValueError("login_id must be an email, a 10-digit Indian mobile number, or a 4-32 character username")
    return v


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RegisterIn(Strict):
    login_id: str = Field(min_length=4, max_length=120, description="Email, mobile number or username")
    full_name: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=1, max_length=128)
    preferred_language: Literal["en", "hi"] = "en"
    consent_accepted: bool = Field(description="User accepted the plain-language data notice")
    consent_mode: Literal["save_profile", "use_once"] = "save_profile"

    @field_validator("login_id")
    @classmethod
    def _login(cls, v: str) -> str:
        return _norm_login(v)

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: str) -> str:
        if any(ch in v for ch in "<>{}$"):
            raise ValueError("name contains invalid characters")
        return v


class LoginIn(Strict):
    login_id: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=128)
    portal: Portal = "user"
    otp: str | None = Field(default=None, max_length=10, description="Authenticator code, if 2FA is on")

    @field_validator("login_id")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class RefreshIn(Strict):
    refresh_token: str = Field(min_length=10, max_length=2048)


class ChangePasswordIn(Strict):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class ResetRequestIn(Strict):
    login_id: str = Field(min_length=3, max_length=120)

    @field_validator("login_id")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class ResetConfirmIn(Strict):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=1, max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: Role
    expires_in: int


class UserOut(BaseModel):
    id: str
    login_id: str
    full_name: str
    role: Role
    status: AccountStatus
    preferred_language: Literal["en", "hi"] = "en"
    created_at: datetime
    consent: dict | None = None
    totp_enabled: bool = False


class OfficerCreateIn(Strict):
    login_id: str = Field(min_length=4, max_length=120)
    full_name: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=1, max_length=128)
    region: str = Field(default="", max_length=60)
    assigned_schemes: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("login_id")
    @classmethod
    def _login(cls, v: str) -> str:
        return _norm_login(v)


class OfficerStatusIn(Strict):
    status: AccountStatus
    reason: str = Field(min_length=3, max_length=300)


class OfficerOut(BaseModel):
    id: str
    login_id: str
    full_name: str
    status: AccountStatus
    region: str
    assigned_schemes: list[str]
    created_at: datetime
    created_by: str | None = None
