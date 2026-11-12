"""Saved citizen profile: eligibility answers (used by the rules engine) + personal details."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.scheme import EligibilityProfile

_TWELVE_DIGITS = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")


def _no_full_ids(v: str | None) -> str | None:
    if v and _TWELVE_DIGITS.search(v):
        raise ValueError("looks like a full Aadhaar number; only the last four digits may be stored")
    return v


class PersonalInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str | None = Field(default=None, max_length=80)
    date_of_birth: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    aadhaar_last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    district: str | None = Field(default=None, max_length=60)
    address: str | None = Field(default=None, max_length=200)
    bank_account_last4: str | None = Field(default=None, pattern=r"^\d{4}$")

    @field_validator("full_name", "district", "address")
    @classmethod
    def _ids(cls, v: str | None) -> str | None:
        return _no_full_ids(v)


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eligibility: EligibilityProfile = Field(default_factory=EligibilityProfile)
    personal: PersonalInfo = Field(default_factory=PersonalInfo)


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["save_profile", "use_once"]
    accepted: bool


class DeleteAccountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=128)
    confirm: Literal["DELETE"]


class ConfirmFieldsIn(BaseModel):
    """Fields the user reviewed (and possibly corrected) on one document, to copy into the profile."""

    model_config = ConfigDict(extra="forbid")
    fields: dict[str, Any] = Field(default_factory=dict, max_length=30)


DOC_TYPES = Literal["aadhaar", "income_certificate", "caste_certificate", "ration_card", "bpl_card",
                    "disability_certificate", "marksheet", "bank_passbook", "land_record", "birth_certificate",
                    "age_proof", "domicile_certificate", "other"]
