"""Step 8 features: adaptive interview, what-if, planner (+PDF), scam check, updates feed, speech-to-text."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.ai import speech
from app.db import get_db
from app.models.scheme import EligibilityProfile
from app.security import rate_limit
from app.security.deps import AnyUser, UserOnly
from app.services import interview, planner, profiles, scam, updates, whatif

router = APIRouter(prefix="/api", tags=["features"])


class InterviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: EligibilityProfile = Field(default_factory=EligibilityProfile)
    skipped: list[str] = Field(default_factory=list, max_length=30)


class WhatIfIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheme: str | None = Field(default=None, max_length=80)
    profile: EligibilityProfile | None = None


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: EligibilityProfile | None = None
    include_maybe: bool = False


class ScamIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=5, max_length=3000)


async def _profile(user: Any, given: EligibilityProfile | None) -> dict[str, Any]:
    if given is not None:
        return given.model_dump(exclude_none=True)
    return (await profiles.get_profile(get_db(), user.id))["eligibility"]


@router.post("/interview/next")
async def interview_next(body: InterviewIn, user: UserOnly) -> dict[str, Any]:
    return await interview.next_question(get_db(), body.answers.model_dump(exclude_none=True), body.skipped)


@router.post("/whatif")
async def what_if(body: WhatIfIn, user: UserOnly) -> Any:
    profile = await _profile(user, body.profile)
    if body.scheme:
        return await whatif.for_scheme(get_db(), body.scheme, profile)
    return {"closest": await whatif.closest(get_db(), profile)}


@router.post("/plan")
async def make_plan(body: PlanIn, user: UserOnly) -> dict[str, Any]:
    return await planner.plan(get_db(), await _profile(user, body.profile), user.id, body.include_maybe)


@router.get("/plan/pdf")
async def plan_pdf(user: UserOnly, include_maybe: bool = False) -> Response:
    p = await planner.plan(get_db(), await _profile(user, None), user.id, include_maybe)
    return Response(content=planner.pdf(p, user.full_name), media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="scheme-plan.pdf"', "Cache-Control": "no-store"})


@router.post("/scam-check")
async def scam_check(body: ScamIn, request: Request) -> dict[str, Any]:
    """Public: anyone can check a suspicious message. The text is not stored."""
    rate_limit.enforce(request, "scam", 30)
    return await scam.check(get_db(), body.text)


@router.get("/updates")
async def updates_feed() -> list[dict[str, Any]]:
    return await updates.feed(get_db())


@router.get("/voice/status")
async def voice_status() -> dict[str, Any]:
    return speech.status()


@router.post("/stt")
async def stt(user: AnyUser, request: Request, file: Annotated[UploadFile, File()],
              language: Annotated[str | None, Form(max_length=5)] = None) -> dict[str, Any]:
    rate_limit.enforce(request, "stt", 20)
    data = await file.read(speech.MAX_BYTES + 1)
    import asyncio

    return await asyncio.to_thread(speech.transcribe, data, language)
