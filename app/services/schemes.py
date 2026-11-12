"""Scheme catalogue: reads, search/filter, matching via the rules engine, and versioned admin edits."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services import audit, eligibility
from app.services.errors import ServiceError

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_HIDDEN = {"_id", "deleted_at", "deleted_by", "deleted_reason"}


def load_seed_files() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    schemes = json.loads((DATA_DIR / "schemes.json").read_text(encoding="utf-8"))
    catalog = json.loads((DATA_DIR / "documents_catalog.json").read_text(encoding="utf-8"))
    return schemes, catalog


def public(doc: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in doc.items() if k not in _HIDDEN}
    out["id"] = doc["slug"]
    return out


async def _catalog(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    return {d["_id"]: {"name": d["name"], "where": d["where"]} async for d in db.required_documents.find()}


async def list_schemes(
    db: AsyncIOMotorDatabase, *, q: str | None = None, category: str | None = None, level: str | None = None,
    benefit_type: str | None = None, group: str | None = None, state: str | None = None,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"deleted_at": None}
    if category:
        query["category"] = category
    if level:
        query["level"] = level
    if benefit_type:
        query["benefit_type"] = benefit_type
    if group:
        query["beneficiary_groups"] = group
    if state:
        query["$or"] = [{"level": "central"}, {"state": state}]
    rows = [public(d) async for d in db.schemes.find(query).sort("name.en", 1)]
    if q:
        rows = [r for r in rows if _keyword_score(r, q) > 0]
        rows.sort(key=lambda r: -_keyword_score(r, q))
    return rows


def _keyword_score(scheme: dict[str, Any], q: str) -> int:
    """Keyword search over English and Hindi text. (Meaning-based search is added with RAG in step 4.)"""
    terms = [t for t in re.split(r"\s+", q.strip().lower()) if t]
    if not terms:
        return 1
    hay_name = f"{scheme['name']['en']} {scheme['name']['hi']}".lower()
    hay = " ".join([hay_name, scheme["summary"]["en"], scheme["summary"]["hi"], scheme["benefit"]["en"],
                    scheme["benefit"]["hi"], " ".join(scheme.get("tags", [])), scheme["category"]]).lower()
    score = 0
    for t in terms:
        if t in hay_name:
            score += 3
        elif t in hay:
            score += 1
        else:
            return 0  # every term must appear somewhere
    return score


async def get_scheme(db: AsyncIOMotorDatabase, slug: str) -> dict[str, Any]:
    doc = await db.schemes.find_one({"slug": slug, "deleted_at": None})
    if not doc:
        raise ServiceError("scheme_not_found", 404)
    out = public(doc)
    catalog = await _catalog(db)
    out["documents_detail"] = [{"id": d, **catalog.get(d, {"name": {"en": d, "hi": d}, "where": None})}
                               for d in doc.get("documents", [])]
    names = {d["slug"]: d["name"] async for d in db.schemes.find(
        {"slug": {"$in": doc.get("exclusive_with", [])}, "deleted_at": None}, {"slug": 1, "name": 1})}
    out["exclusive_with_detail"] = [{"slug": s, "name": names[s]} for s in doc.get("exclusive_with", []) if s in names]
    out["rule_fields"] = sorted({leaf["field"] for leaf in eligibility.iter_leaves(doc["rules"])})
    out["rule_list"] = [{"id": leaf["id"], "text": {"en": eligibility.describe_rule(leaf, "en"),
                                                    "hi": eligibility.describe_rule(leaf, "hi")},
                         "note": leaf.get("note")} for leaf in eligibility.iter_leaves(doc["rules"])]
    return out


async def stats(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    count = await db.schemes.count_documents({"deleted_at": None})
    latest = await db.schemes.find_one({"deleted_at": None}, sort=[("last_verified", -1)])
    return {"schemes": count, "last_verified": latest["last_verified"] if latest else None}


async def match(db: AsyncIOMotorDatabase, profile: dict[str, Any], *, category: str | None = None,
                level: str | None = None, benefit_type: str | None = None,
                include_not_eligible: bool = True) -> dict[str, Any]:
    schemes = await list_schemes(db, category=category, level=level, benefit_type=benefit_type)
    results = []
    for s in schemes:
        if s["level"] == "state" and profile.get("state") not in (None, s["state"]):
            continue  # another state's scheme is irrelevant, not "not eligible"
        ev = eligibility.evaluate(s["rules"], profile)
        if ev.status == "not_eligible" and not include_not_eligible:
            continue
        results.append((eligibility.rank_key(ev, s["annual_benefit_inr"]), s, ev))
    results.sort(key=lambda r: r[0], reverse=True)
    items = [{
        "scheme": {k: s[k] for k in ("id", "slug", "name", "summary", "benefit", "category", "level", "state",
                                     "benefit_type", "annual_benefit_inr", "official_url", "last_verified",
                                     "version", "deadline")},
        "result": ev.to_dict(),
    } for _, s, ev in results]
    counts = {k: sum(1 for i in items if i["result"]["status"] == k) for k in ("eligible", "maybe", "not_eligible")}
    return {"counts": counts, "items": items, "profile_used": eligibility.clean_profile(profile),
            "engine": "rules-v1 (deterministic)"}


async def check_one(db: AsyncIOMotorDatabase, slug: str, profile: dict[str, Any]) -> dict[str, Any]:
    doc = await db.schemes.find_one({"slug": slug, "deleted_at": None})
    if not doc:
        raise ServiceError("scheme_not_found", 404)
    return {"scheme_id": slug, "version": doc["version"], **eligibility.evaluate(doc["rules"], profile).to_dict()}

# ----------------------------------------------------------------------------- admin edits


async def _validate_refs(db: AsyncIOMotorDatabase, data: dict[str, Any]) -> None:
    catalog = await _catalog(db)
    missing = [d for d in data.get("documents", []) if d not in catalog]
    if missing:
        raise ServiceError("unknown_documents", 422, documents=missing)
    for other in data.get("exclusive_with", []):
        if other == data["slug"]:
            raise ServiceError("self_exclusion", 422)
        if not await db.schemes.find_one({"slug": other, "deleted_at": None}, {"_id": 1}):
            raise ServiceError("unknown_exclusive_scheme", 422, slug=other)


def _jsonable(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


async def _snapshot(db: AsyncIOMotorDatabase, doc: dict[str, Any], actor: dict[str, Any], note: str) -> None:
    data = {k: v for k, v in doc.items() if k != "_id"}
    await db.scheme_versions.insert_one({
        "_id": uuid.uuid4().hex, "scheme_id": doc["_id"], "slug": doc["slug"], "version": doc["version"],
        "data": data, "changed_by": actor.get("id"), "changed_at": datetime.now(UTC), "change_note": note,
    })


async def insert_scheme(db: AsyncIOMotorDatabase, data: dict[str, Any], actor: dict[str, Any], *,
                        note: str = "created", last_verified: str | None = None,
                        data_status: str = "admin") -> dict[str, Any]:
    doc = {
        "_id": uuid.uuid4().hex, **data, "version": 1,
        "last_verified": last_verified or date.today().isoformat(), "data_status": data_status,
        "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC), "deleted_at": None,
    }
    await db.schemes.insert_one(doc)
    await _snapshot(db, doc, actor, note)
    return doc


async def create_scheme(db: AsyncIOMotorDatabase, body: Any, actor: dict[str, Any]) -> dict[str, Any]:
    data = _jsonable(body)
    if await db.schemes.find_one({"slug": data["slug"]}, {"_id": 1}):
        raise ServiceError("slug_taken", 409)
    await _validate_refs(db, data)
    doc = await insert_scheme(db, data, actor)
    await audit.record(db, actor=actor, action="scheme.create", target={"type": "scheme", "id": data["slug"]},
                       after={"version": 1, "rules": data["rules"]})
    return public(doc)


async def update_scheme(db: AsyncIOMotorDatabase, slug: str, body: Any, actor: dict[str, Any]) -> dict[str, Any]:
    data = _jsonable(body)
    note = data.pop("change_note")
    if data["slug"] != slug:
        raise ServiceError("slug_immutable", 422)
    current = await db.schemes.find_one({"slug": slug, "deleted_at": None})
    if not current:
        raise ServiceError("scheme_not_found", 404)
    await _validate_refs(db, data)
    new_version = current["version"] + 1
    update = {**data, "version": new_version, "updated_at": datetime.now(UTC),
              "last_verified": date.today().isoformat(), "data_status": "admin"}
    await db.schemes.update_one({"_id": current["_id"], "version": current["version"]}, {"$set": update})
    doc = await db.schemes.find_one({"_id": current["_id"]})
    await _snapshot(db, doc, actor, note)
    changed = sorted(k for k in data if current.get(k) != data[k])
    await audit.record(
        db, actor=actor, action="scheme.update", target={"type": "scheme", "id": slug}, reason=note,
        before={"version": current["version"], **{k: current.get(k) for k in changed}},
        after={"version": new_version, **{k: data[k] for k in changed}},
    )
    if "rules" in changed:
        from app.services import updates

        await updates.notify_affected(db, slug, current["rules"], data["rules"], data["name"])
    return public(doc)


async def soft_delete_scheme(db: AsyncIOMotorDatabase, slug: str, reason: str, actor: dict[str, Any]) -> None:
    current = await db.schemes.find_one({"slug": slug, "deleted_at": None})
    if not current:
        raise ServiceError("scheme_not_found", 404)
    await db.schemes.update_one({"_id": current["_id"]}, {"$set": {
        "deleted_at": datetime.now(UTC), "deleted_by": actor["id"], "deleted_reason": reason}})
    await audit.record(db, actor=actor, action="scheme.delete", target={"type": "scheme", "id": slug},
                       before={"version": current["version"]}, reason=reason)


async def list_versions(db: AsyncIOMotorDatabase, slug: str) -> list[dict[str, Any]]:
    rows = [{k: v for k, v in r.items() if k != "_id"}
            async for r in db.scheme_versions.find({"slug": slug}).sort("version", -1)]
    if not rows:
        raise ServiceError("scheme_not_found", 404)
    return rows


async def get_version(db: AsyncIOMotorDatabase, slug: str, version: int) -> dict[str, Any] | None:
    """Applications (step 5) are judged against the version they were submitted under."""
    row = await db.scheme_versions.find_one({"slug": slug, "version": version})
    return row["data"] if row else None
