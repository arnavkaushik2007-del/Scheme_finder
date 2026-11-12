"""/api/admin: admin-only endpoints. Step 1-2 scope: officers, schemes + rule versions, audit log.
The full admin console (users, database browser, analytics) arrives in step 7."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from app.db import get_db
from app.models.scheme import SchemeIn, SchemeUpdateIn
from app.models.user import OfficerCreateIn, OfficerOut, OfficerStatusIn
from app.security.deps import AdminOnly
from app.services import audit, schemes, users

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/officers", response_model=list[OfficerOut])
async def list_officers(admin: AdminOnly) -> Any:
    return await users.list_officers(get_db())


@router.post("/officers", response_model=OfficerOut, status_code=status.HTTP_201_CREATED)
async def create_officer(body: OfficerCreateIn, admin: AdminOnly) -> Any:
    return await users.create_officer(get_db(), body, admin.actor)


@router.patch("/officers/{officer_id}/status", response_model=OfficerOut)
async def set_officer_status(officer_id: str, body: OfficerStatusIn, admin: AdminOnly) -> Any:
    return await users.set_officer_status(get_db(), officer_id, body.status, body.reason, admin.actor)


def _reindex() -> None:
    """Scheme text changed: rebuild the assistant's retrieval index in the background."""
    import asyncio

    from app.ai import index

    index.STATE["ready"] = False
    asyncio.get_running_loop().create_task(index.rebuild(get_db()))


@router.post("/schemes", status_code=status.HTTP_201_CREATED)
async def create_scheme(body: SchemeIn, admin: AdminOnly) -> dict[str, Any]:
    out = await schemes.create_scheme(get_db(), body, admin.actor)
    _reindex()
    return out


@router.put("/schemes/{slug}")
async def update_scheme(slug: str, body: SchemeUpdateIn, admin: AdminOnly) -> dict[str, Any]:
    out = await schemes.update_scheme(get_db(), slug, body, admin.actor)
    _reindex()
    return out


@router.delete("/schemes/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scheme(slug: str, admin: AdminOnly,
                        reason: Annotated[str, Query(min_length=3, max_length=300)]) -> None:
    await schemes.soft_delete_scheme(get_db(), slug, reason, admin.actor)
    _reindex()


@router.get("/ai/queue")
async def ai_queue(admin: AdminOnly, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[dict[str, Any]]:
    """Low-confidence queue: answers the guard declined, plus answers users marked thumbs-down."""
    q = {"$or": [{"abstained": True}, {"feedback.rating": "down"}]}
    return [{k: v for k, v in r.items() if k != "user_id"}
            async for r in get_db().chat_logs.find(q).sort("created_at", -1).limit(limit)]


@router.get("/schemes/{slug}/versions")
async def scheme_versions(slug: str, admin: AdminOnly) -> list[dict[str, Any]]:
    return await schemes.list_versions(get_db(), slug)


@router.get("/audit")
async def audit_log(admin: AdminOnly, limit: Annotated[int, Query(ge=1, le=500)] = 100,
                    skip: Annotated[int, Query(ge=0)] = 0,
                    action: Annotated[str | None, Query(max_length=60)] = None,
                    role: Annotated[str | None, Query(pattern="^(admin|officer|user|system)$")] = None,
                    target: Annotated[str | None, Query(max_length=64)] = None) -> dict[str, Any]:
    import re

    db = get_db()
    q: dict[str, Any] = {}
    if action:
        q["action"] = {"$regex": "^" + re.escape(action)}
    if role:
        q["actor.role"] = role
    if target:
        q["target.id"] = target
    rows = [{k: v for k, v in e.items() if k != "_id"}
            async for e in db.audit_log.find(q).sort("seq", -1).skip(skip).limit(limit)]
    ok, bad = await audit.verify_chain(db)
    return {"entries": rows, "chain_ok": ok, "first_bad_seq": bad, "total": await db.audit_log.count_documents(q)}


@router.get("/stats")
async def stats(admin: AdminOnly) -> dict[str, int]:
    db = get_db()
    return {
        "users": await db.users.count_documents({"role": "user", "deleted_at": None}),
        "officers": await db.officers.count_documents({}),
        "schemes": await db.schemes.count_documents({"deleted_at": None}),
        "audit_entries": await db.audit_log.count_documents({}),
    }


# ============================================================================ step 7: admin console

from fastapi import File, Form, UploadFile  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from app.services import admin_console, fraud, rules_review  # noqa: E402


class Reason(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=5, max_length=500)


class OfficerUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    full_name: str | None = Field(default=None, min_length=2, max_length=80)
    region: str | None = Field(default=None, max_length=60)
    assigned_schemes: list[str] | None = Field(default=None, max_length=100)
    reason: str = Field(min_length=5, max_length=500)


class UserStatusIn(Reason):
    status: str = Field(pattern="^(active|suspended)$")


class RevealIn(Reason):
    pass


class ProposalDecisionIn(Reason):
    approve: bool
    rules: dict[str, Any] | None = None  # admin may edit the proposed rules before approving


@router.patch("/officers/{officer_id}")
async def update_officer(officer_id: str, body: OfficerUpdateIn, admin: AdminOnly) -> dict[str, Any]:
    data = body.model_dump(exclude_none=True)
    return await admin_console.update_officer(get_db(), officer_id, data, admin.actor)


@router.post("/officers/{officer_id}/reset-password")
async def reset_officer_password(officer_id: str, body: Reason, admin: AdminOnly) -> dict[str, str]:
    temp = await admin_console.reset_officer_password(get_db(), officer_id, body.reason, admin.actor)
    return {"temporary_password": temp, "note": "Shown once. Give it to the officer in person; 2FA was reset too."}


@router.delete("/officers/{officer_id}")
async def delete_officer(officer_id: str, admin: AdminOnly,
                         reason: Annotated[str, Query(min_length=5, max_length=500)]) -> dict[str, int]:
    return {"cases_unassigned": await admin_console.delete_officer(get_db(), officer_id, reason, admin.actor)}


@router.get("/users")
async def list_users(admin: AdminOnly, q: Annotated[str | None, Query(max_length=100)] = None,
                     status: Annotated[str | None, Query(pattern="^(active|suspended)$")] = None,
                     flagged: bool = False) -> list[dict[str, Any]]:
    return await admin_console.list_users(get_db(), q, status, flagged)


@router.get("/users/{user_id}")
async def user_detail(user_id: str, admin: AdminOnly) -> dict[str, Any]:
    return await admin_console.user_detail(get_db(), user_id)


@router.patch("/users/{user_id}/status")
async def user_status(user_id: str, body: UserStatusIn, admin: AdminOnly) -> dict[str, Any]:
    return await admin_console.set_user_status(get_db(), user_id, body.status, body.reason, admin.actor)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, admin: AdminOnly,
                      reason: Annotated[str, Query(min_length=5, max_length=500)]) -> None:
    await admin_console.delete_user(get_db(), user_id, reason, admin.actor)


@router.post("/fraud/scan")
async def fraud_scan(admin: AdminOnly) -> dict[str, Any]:
    return await fraud.scan(get_db(), admin.actor)


@router.post("/fraud/{user_id}/clear", status_code=status.HTTP_204_NO_CONTENT)
async def fraud_clear(user_id: str, body: Reason, admin: AdminOnly) -> None:
    await fraud.clear(get_db(), user_id, body.reason, admin.actor)


@router.get("/database/{collection}")
async def browse(collection: str, admin: AdminOnly, q: Annotated[str | None, Query(max_length=100)] = None,
                 skip: Annotated[int, Query(ge=0)] = 0,
                 limit: Annotated[int, Query(ge=1, le=100)] = 20) -> dict[str, Any]:
    return await admin_console.browse(get_db(), collection, q=q, skip=skip, limit=limit)


@router.post("/database/{collection}/{doc_id}/reveal")
async def reveal(collection: str, doc_id: str, body: RevealIn, admin: AdminOnly) -> dict[str, Any]:
    return await admin_console.reveal(get_db(), collection, doc_id, body.reason, admin.actor)


@router.get("/analytics")
async def analytics(admin: AdminOnly) -> dict[str, Any]:
    return await admin_console.analytics(get_db())


@router.post("/rules/ingest", status_code=status.HTTP_201_CREATED)
async def ingest(admin: AdminOnly, scheme: Annotated[str, Form(min_length=3, max_length=80)],
                 text: Annotated[str | None, Form(max_length=200_000)] = None,
                 file: Annotated[UploadFile | None, File()] = None) -> dict[str, Any]:
    data = await file.read(10 * 1024 * 1024 + 1) if file else None
    if data and len(data) > 10 * 1024 * 1024:
        from app.services.errors import ServiceError

        raise ServiceError("file_too_large", 413, max_mb=10)
    body, sha = rules_review.guideline_text(data, text)
    return await rules_review.ingest(get_db(), scheme, body, sha, file.filename if file else None, admin.actor)


@router.get("/rules/proposals")
async def proposals(admin: AdminOnly,
                    status_: Annotated[str | None, Query(alias="status", pattern="^(pending|approved|rejected)$")] = None
                    ) -> list[dict[str, Any]]:
    return await rules_review.list_proposals(get_db(), status_)


@router.post("/rules/proposals/{pid}/decision")
async def decide_proposal(pid: str, body: ProposalDecisionIn, admin: AdminOnly) -> dict[str, Any]:
    out = await rules_review.decide(get_db(), pid, body.approve, body.reason, body.rules, admin.actor)
    if body.approve:
        _reindex()
    return out


@router.get("/schemes-all")
async def schemes_with_rules(admin: AdminOnly) -> list[dict[str, Any]]:
    """Full scheme records (with rules) for the admin editor."""
    return [schemes.public(s) async for s in get_db().schemes.find({"deleted_at": None}).sort("name.en", 1)]


# ============================================================================ step 8: AI evaluation dashboard

@router.post("/ai/evaluate")
async def ai_evaluate(admin: AdminOnly) -> dict[str, Any]:
    from app.services import evaluation

    return await evaluation.run(get_db(), admin.actor)


@router.get("/ai/evaluations")
async def ai_evaluations(admin: AdminOnly) -> dict[str, Any]:
    from app.services import evaluation

    return {"runs": await evaluation.history(get_db()), "versions": evaluation.versions()}
