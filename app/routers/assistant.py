"""/api/ask (RAG chat), feedback, /api/search (hybrid scheme search), /api/recommendations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.ai import assistant, index
from app.ai.embeddings import get_embedder
from app.ai.llm import get_llm
from app.db import get_db
from app.models.scheme import EligibilityProfile
from app.security import rate_limit
from app.security.deps import AnyUser, UserOnly
from app.services import profiles, recommend, schemes
from app.services.errors import ServiceError

router = APIRouter(prefix="/api", tags=["assistant"])


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=2, max_length=1000)
    scheme: str | None = Field(default=None, max_length=80)
    profile: EligibilityProfile | None = None  # "use once" answers; otherwise the saved profile is used


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=500)


class RecommendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: EligibilityProfile | None = None
    include_not_eligible: bool = True


@router.get("/assistant/status")
async def status(user: AnyUser) -> dict[str, Any]:
    return {"index": {k: v for k, v in index.STATE.items()}, "llm": get_llm().name,
            "mode": "extractive" if get_llm().name == "mock" else "generative",
            "embedder": index.STATE.get("embedder") or "not loaded", "store": index.STATE.get("store")}


@router.post("/ask")
async def ask(body: AskIn, user: AnyUser, request: Request) -> dict[str, Any]:
    rate_limit.enforce(request, "ask", 30)
    db = get_db()
    if body.scheme and not await db.schemes.find_one({"slug": body.scheme, "deleted_at": None}, {"_id": 1}):
        raise ServiceError("scheme_not_found", 404)
    profile = body.profile.model_dump(exclude_none=True) if body.profile else None
    if profile is None and user.role == "user":
        profile = (await profiles.get_profile(db, user.id))["eligibility"] or None
    return await assistant.ask(db, question=body.question, user={"id": user.id, "role": user.role},
                               scheme_slug=body.scheme, profile=profile)


@router.post("/ask/{log_id}/feedback")
async def feedback(log_id: str, body: FeedbackIn, user: AnyUser) -> dict[str, str]:
    db = get_db()
    res = await db.chat_logs.update_one({"_id": log_id, "user_id": user.id}, {"$set": {
        "feedback": {"rating": body.rating, "comment": body.comment, "at": datetime.now(UTC)}}})
    if res.matched_count == 0:
        raise ServiceError("not_found", 404)
    return {"status": "recorded"}


@router.get("/search")
async def search(q: Annotated[str, Query(min_length=2, max_length=100)]) -> dict[str, Any]:
    """Hybrid scheme search (keyword + multilingual embeddings). Public, like the catalogue."""
    db = get_db()
    semantic = await index.search_schemes(db, q)
    keyword = {r["slug"] for r in await schemes.list_schemes(db, q=q)}
    score = {slug: s for slug, s in semantic}
    for slug in keyword:
        score[slug] = score.get(slug, 0.0) + 0.25
    rows = {r["slug"]: r for r in await schemes.list_schemes(db)}
    ordered = sorted((s for s in score if s in rows), key=lambda s: -score[s])
    return {"query": q, "embedder": get_embedder().name,
            "results": [{"slug": s, "score": round(score[s], 3), "keyword_match": s in keyword} for s in ordered]}


@router.get("/recommendations")
async def recommendations(user: UserOnly, include_not_eligible: bool = True) -> dict[str, Any]:
    prof = await profiles.get_profile(get_db(), user.id)
    return await recommend.recommend(get_db(), prof["eligibility"], include_not_eligible=include_not_eligible)


@router.post("/recommendations")
async def recommendations_for(body: RecommendIn, user: UserOnly) -> dict[str, Any]:
    """Same as GET but for answers given now ("use once"); nothing is stored."""
    profile = body.profile.model_dump(exclude_none=True) if body.profile else \
        (await profiles.get_profile(get_db(), user.id))["eligibility"]
    return await recommend.recommend(get_db(), profile, include_not_eligible=body.include_not_eligible)
