"""/api/applications (citizen side) and /api/notifications."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.db import get_db
from app.models.profile import PersonalInfo
from app.models.scheme import EligibilityProfile
from app.security import rate_limit
from app.security.deps import UserOnly
from app.services import applications as apps
from app.services.errors import ServiceError

router = APIRouter(prefix="/api", tags=["applications"])


class ApplicationForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eligibility: EligibilityProfile = Field(default_factory=EligibilityProfile)
    personal: PersonalInfo = Field(default_factory=PersonalInfo)


class CreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheme: str = Field(min_length=3, max_length=80)
    form: ApplicationForm = Field(default_factory=ApplicationForm)
    documents: list[str] = Field(default_factory=list, max_length=20)


class UpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    form: ApplicationForm
    documents: list[str] = Field(default_factory=list, max_length=20)


class SubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    declaration: bool


class WithdrawIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=300)


class ResubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documents: list[str] = Field(default_factory=list, max_length=20)
    note: str | None = Field(default=None, max_length=500)


def _form(f: ApplicationForm) -> dict[str, Any]:
    return {"eligibility": f.eligibility.model_dump(exclude_none=True),
            "personal": f.personal.model_dump(exclude_none=True)}


@router.get("/applications")
async def list_applications(user: UserOnly) -> list[dict[str, Any]]:
    return await apps.list_for_user(get_db(), user.id)


@router.post("/applications", status_code=status.HTTP_201_CREATED)
async def create(body: CreateIn, user: UserOnly, request: Request) -> dict[str, Any]:
    rate_limit.enforce(request, "applications", 30)
    return apps.public(await apps.create_draft(get_db(), user.id, body.scheme, _form(body.form), body.documents))


@router.get("/applications/{app_id}")
async def get(app_id: str, user: UserOnly) -> dict[str, Any]:
    return apps.public(await apps.own(get_db(), user.id, app_id))


@router.put("/applications/{app_id}")
async def update(app_id: str, body: UpdateIn, user: UserOnly) -> dict[str, Any]:
    return apps.public(await apps.update_draft(get_db(), user.id, app_id, _form(body.form), body.documents))


@router.post("/applications/{app_id}/precheck")
async def run_precheck(app_id: str, user: UserOnly) -> dict[str, Any]:
    return await apps.run_precheck(get_db(), user.id, app_id)


@router.post("/applications/{app_id}/submit")
async def submit(app_id: str, body: SubmitIn, user: UserOnly) -> dict[str, Any]:
    if not body.declaration:
        raise ServiceError("declaration_required", 422)
    return apps.public(await apps.submit(get_db(), user.id, app_id))


@router.post("/applications/{app_id}/withdraw")
async def withdraw(app_id: str, body: WithdrawIn, user: UserOnly) -> dict[str, Any]:
    return apps.public(await apps.withdraw(get_db(), user.id, app_id, body.reason))


@router.post("/applications/{app_id}/resubmit")
async def resubmit(app_id: str, body: ResubmitIn, user: UserOnly) -> dict[str, Any]:
    if not body.documents and not body.note:
        raise ServiceError("nothing_to_resubmit", 422)
    return apps.public(await apps.resubmit(get_db(), user.id, app_id, body.documents, body.note))


@router.get("/notifications")
async def notifications(user: UserOnly) -> dict[str, Any]:
    return await apps.notifications(get_db(), user.id)


@router.post("/notifications/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(user: UserOnly) -> None:
    await apps.mark_read(get_db(), user.id)
