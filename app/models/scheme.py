"""Pydantic models for schemes, rules and eligibility profiles."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.services.eligibility import FIELDS, INDIAN_STATES, RuleError, validate_rules

Category = Literal["health", "pension", "education", "agriculture", "housing", "women_child", "disability",
                   "employment", "financial_inclusion", "insurance", "energy", "social_welfare"]
BenefitType = Literal["cash", "pension", "insurance", "scholarship", "subsidy", "loan", "in_kind", "savings"]
Group = Literal["women", "girls", "elderly", "students", "farmers", "workers", "disabled", "widows", "sc_st",
                "minorities", "entrepreneurs", "families", "youth"]

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class I18n(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    en: str = Field(min_length=1, max_length=2000)
    hi: str = Field(min_length=1, max_length=2000)


class SchemeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=3, max_length=80)
    name: I18n
    summary: I18n
    benefit: I18n
    category: Category
    level: Literal["central", "state"]
    state: str | None = None
    ministry: I18n
    benefit_type: BenefitType
    beneficiary_groups: list[Group] = Field(default_factory=list, max_length=13)
    annual_benefit_inr: int = Field(ge=0, le=100_000_000)
    deadline: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    official_url: HttpUrl
    source_url: HttpUrl
    documents: list[str] = Field(default_factory=list, max_length=30)
    steps: list[I18n] = Field(min_length=1, max_length=12)
    exclusive_with: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=12)
    rules: dict[str, Any]

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise ValueError("slug must be lowercase kebab-case")
        return v

    @field_validator("rules")
    @classmethod
    def _rules(cls, v: dict[str, Any]) -> dict[str, Any]:
        try:
            validate_rules(v)
        except RuleError as exc:
            raise ValueError(f"invalid rules: {exc}") from exc
        return v

    @model_validator(mode="after")
    def _state_level(self) -> "SchemeIn":
        if self.level == "state" and self.state not in INDIAN_STATES:
            raise ValueError("state schemes need a valid state")
        if self.level == "central" and self.state is not None:
            raise ValueError("central schemes must not set a state")
        return self


class SchemeUpdateIn(SchemeIn):
    change_note: str = Field(min_length=3, max_length=500, description="Why the scheme or its rules changed")

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        return SchemeIn._slug(v)


class EligibilityProfile(BaseModel):
    """What a person tells us about themselves. Every field is optional: missing = unknown."""

    model_config = ConfigDict(extra="forbid")

    age: int | None = Field(default=None, ge=0, le=120)
    gender: Literal["male", "female", "transgender"] | None = None
    state: str | None = None
    residence: Literal["rural", "urban"] | None = None
    category: Literal["general", "obc", "sc", "st", "ews"] | None = None
    is_minority: bool | None = None
    annual_income: int | None = Field(default=None, ge=0, le=1_000_000_000)
    monthly_income: int | None = Field(default=None, ge=0, le=100_000_000)
    occupation: Literal["farmer", "agricultural_labourer", "unorganised_worker", "student", "salaried",
                        "self_employed", "street_vendor", "artisan", "fisher", "unemployed", "homemaker",
                        "retired"] | None = None
    is_bpl: bool | None = None
    marital_status: Literal["single", "married", "widowed", "divorced"] | None = None
    disability_percent: int | None = Field(default=None, ge=0, le=100)
    land_holding_hectares: float | None = Field(default=None, ge=0, le=10_000)
    is_income_tax_payer: bool | None = None
    is_govt_employee: bool | None = None
    has_pucca_house: bool | None = None
    has_bank_account: bool | None = None
    education_level: Literal["none", "primary", "secondary", "higher_secondary", "graduate",
                             "postgraduate"] | None = None
    marks_percent: float | None = Field(default=None, ge=0, le=100)
    is_pregnant_or_lactating: bool | None = None
    has_girl_child_under_10: bool | None = None
    family_size: int | None = Field(default=None, ge=1, le=50)
    has_lpg_connection: bool | None = None
    has_epf_esic_nps: bool | None = None

    @field_validator("state")
    @classmethod
    def _state(cls, v: str | None) -> str | None:
        if v is not None and v not in INDIAN_STATES:
            raise ValueError("unknown state")
        return v


assert set(EligibilityProfile.model_fields) == set(FIELDS), "profile model and rules vocabulary drifted apart"


class MatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: EligibilityProfile
    category: Category | None = None
    level: Literal["central", "state"] | None = None
    benefit_type: BenefitType | None = None
    include_not_eligible: bool = True
