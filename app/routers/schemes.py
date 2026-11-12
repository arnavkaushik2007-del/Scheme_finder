"""/api/schemes (public catalogue reads) and /api/match (rules engine, signed-in users)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from app.config import get_settings
from app.db import get_db
from app.models.scheme import BenefitType, Category, EligibilityProfile, Group, MatchIn
from app.security import rate_limit
from app.security.deps import AnyUser, UserOnly
from app.services import eligibility, schemes

router = APIRouter(prefix="/api", tags=["schemes"])


@router.get("/schemes")
async def list_schemes(
    q: Annotated[str | None, Query(max_length=100)] = None,
    category: Category | None = None,
    level: Annotated[str | None, Query(pattern="^(central|state)$")] = None,
    benefit_type: BenefitType | None = None,
    group: Group | None = None,
    state: Annotated[str | None, Query(max_length=60)] = None,
) -> list[dict[str, Any]]:
    """Scheme information is public information, so reading the catalogue needs no login."""
    rows = await schemes.list_schemes(get_db(), q=q, category=category, level=level, benefit_type=benefit_type,
                                      group=group, state=state)
    return [{k: r[k] for k in ("id", "slug", "name", "summary", "benefit", "category", "level", "state",
                               "benefit_type", "beneficiary_groups", "annual_benefit_inr", "deadline",
                               "official_url", "last_verified", "data_status", "version", "tags")} for r in rows]


@router.get("/schemes/stats")
async def scheme_stats() -> dict[str, Any]:
    return await schemes.stats(get_db())


@router.get("/schemes/vocabulary")
async def vocabulary() -> dict[str, Any]:
    """Field names, value lists and labels used by rules and profile forms (both languages)."""
    return {
        "fields": {k: {"kind": v.kind, "label": {"en": v.en, "hi": v.hi}, "unit": v.unit, "min": v.min,
                       "max": v.max} for k, v in eligibility.FIELDS.items()},
        "enums": eligibility.ENUMS,
        "states": eligibility.INDIAN_STATES,
        "ops": list(eligibility.OPS),
    }


@router.get("/schemes/{slug}")
async def get_scheme(slug: str) -> dict[str, Any]:
    return await schemes.get_scheme(get_db(), slug)


@router.post("/schemes/{slug}/check")
async def check_scheme(slug: str, profile: EligibilityProfile, user: AnyUser) -> dict[str, Any]:
    return await schemes.check_one(get_db(), slug, profile.model_dump(exclude_none=True))


@router.post("/match")
async def match(body: MatchIn, user: UserOnly, request: Request) -> dict[str, Any]:
    """Rank every scheme for a profile. Personal data is processed in memory and not stored here."""
    rate_limit.enforce(request, "match", 60)
    return await schemes.match(get_db(), body.profile.model_dump(exclude_none=True), category=body.category,
                               level=body.level, benefit_type=body.benefit_type,
                               include_not_eligible=body.include_not_eligible)
