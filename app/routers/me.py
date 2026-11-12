"""/api/me: the signed-in citizen's own profile, consent, saved schemes and data deletion."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status

from app.db import get_db
from app.models.profile import ConsentIn, DeleteAccountIn, ProfileIn
from app.security.deps import UserOnly
from app.services import profiles

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("/profile")
async def get_profile(user: UserOnly) -> dict[str, Any]:
    return await profiles.get_profile(get_db(), user.id)


@router.put("/profile")
async def put_profile(body: ProfileIn, user: UserOnly) -> dict[str, Any]:
    return await profiles.save_profile(get_db(), user.id, body.eligibility.model_dump(exclude_none=True),
                                       body.personal.model_dump(exclude_none=True))


@router.delete("/profile", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(user: UserOnly) -> None:
    await profiles.clear_profile(get_db(), user.id)


@router.put("/consent")
async def set_consent(body: ConsentIn, user: UserOnly) -> dict[str, Any]:
    return await profiles.set_consent(get_db(), user.id, body.mode, body.accepted)


@router.post("/consent/withdraw")
async def withdraw_consent(user: UserOnly) -> dict[str, Any]:
    return await profiles.withdraw_consent(get_db(), user.id)


@router.post("/saved-schemes/{slug}")
async def save_scheme(slug: str, user: UserOnly) -> dict[str, Any]:
    return {"saved_schemes": await profiles.toggle_saved(get_db(), user.id, slug, True)}


@router.delete("/saved-schemes/{slug}")
async def unsave_scheme(slug: str, user: UserOnly) -> dict[str, Any]:
    return {"saved_schemes": await profiles.toggle_saved(get_db(), user.id, slug, False)}


@router.get("/checklist")
async def checklist(user: UserOnly) -> list[dict[str, Any]]:
    return await profiles.checklist(get_db(), user.id)


@router.post("/delete-account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(body: DeleteAccountIn, user: UserOnly) -> None:
    await profiles.delete_account(get_db(), user.id, body.password, user.ip)
