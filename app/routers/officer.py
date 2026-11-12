"""/api/officer: queue, case view, AI report, document viewer, decisions, history and stats (officers only)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.db import get_db
from app.security.deps import OfficerOnly
from app.services import officer

router = APIRouter(prefix="/api/officer", tags=["officer"])


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: Literal["approve", "reject", "request_docs"]
    reason: str = Field(min_length=10, max_length=1000, description="Mandatory, shown to the applicant")
    requested_documents: list[str] = Field(default_factory=list, max_length=20)


@router.get("/queue")
async def queue(user: OfficerOnly, status: Annotated[str | None, Query(max_length=30)] = None,
                scheme: Annotated[str | None, Query(max_length=80)] = None,
                min_conf: Annotated[float | None, Query(ge=0, le=1)] = None,
                max_conf: Annotated[float | None, Query(ge=0, le=1)] = None) -> dict[str, Any]:
    return await officer.queue(get_db(), user.id, status=status, scheme=scheme, min_conf=min_conf, max_conf=max_conf)


@router.get("/applications/{app_id}")
async def case(app_id: str, user: OfficerOnly) -> dict[str, Any]:
    return await officer.case(get_db(), user.id, app_id)


@router.post("/applications/{app_id}/claim")
async def claim(app_id: str, user: OfficerOnly) -> dict[str, Any]:
    return await officer.claim(get_db(), user.actor, app_id)


@router.post("/applications/{app_id}/start-review")
async def start_review(app_id: str, user: OfficerOnly) -> dict[str, Any]:
    return await officer.start_review(get_db(), user.actor, app_id)


@router.post("/applications/{app_id}/report")
async def regenerate(app_id: str, user: OfficerOnly) -> dict[str, Any]:
    return await officer.regenerate_report(get_db(), user.actor, app_id)


@router.get("/applications/{app_id}/documents/{doc_id}/file")
async def document_file(app_id: str, doc_id: str, user: OfficerOnly) -> Response:
    data, ctype = await officer.read_document(get_db(), user.actor, app_id, doc_id)
    return Response(content=data, media_type=ctype, headers={"Content-Disposition": "inline",
                                                             "Cache-Control": "no-store"})


@router.post("/applications/{app_id}/decision")
async def decide(app_id: str, body: DecisionIn, user: OfficerOnly) -> dict[str, Any]:
    return await officer.decide(get_db(), user.actor, app_id, body.action, body.reason, body.requested_documents)


@router.post("/applications/{app_id}/close")
async def close(app_id: str, user: OfficerOnly) -> dict[str, Any]:
    return await officer.close(get_db(), user.actor, app_id)


@router.get("/history")
async def history(user: OfficerOnly) -> list[dict[str, Any]]:
    return await officer.history(get_db(), user.id)


@router.get("/stats")
async def stats(user: OfficerOnly) -> dict[str, Any]:
    return await officer.stats(get_db(), user.id)
