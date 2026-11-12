"""Synthetic seed data: 1 admin, 2 officers, 3 demo users, 30 schemes, the required-documents
catalogue. Idempotent: running it twice does not duplicate anything.

All people below are fictional. Passwords are demo-only and printed in the README.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.scheme import SchemeIn
from app.services import audit, schemes, users

log = logging.getLogger(__name__)

SEED_COMPILED_ON = "2026-11-07"

ADMIN = {"login_id": "admin@schemefinder.test", "full_name": "Asha Verma (Admin)", "password": "Yojana#Root2026"}
OFFICERS = [
    {"login_id": "officer.lucknow@schemefinder.test", "full_name": "Rakesh Yadav", "password": "Review#Desk2026",
     "region": "Uttar Pradesh", "assigned_schemes": []},
    {"login_id": "officer.patna@schemefinder.test", "full_name": "Neha Sinha", "password": "Review#Desk2026",
     "region": "Bihar", "assigned_schemes": []},
]
DEMO_USERS = [
    {"login_id": "sunita@demo.test", "full_name": "Sunita Devi", "password": "Nagrik#Demo2026", "lang": "hi"},
    {"login_id": "ramesh@demo.test", "full_name": "Ramesh Kumar", "password": "Nagrik#Demo2026", "lang": "hi"},
    {"login_id": "priya@demo.test", "full_name": "Priya Sharma", "password": "Nagrik#Demo2026", "lang": "en"},
]


async def _ensure_account(db: AsyncIOMotorDatabase, *, role: str, created_by: str | None = None,
                          consent: dict[str, Any] | None = None, lang: str = "en", **acct: Any) -> dict[str, Any]:
    existing = await db.users.find_one({"login_id": acct["login_id"]})
    if existing:
        return existing
    return await users.create_account(db, login_id=acct["login_id"], full_name=acct["full_name"],
                                      password=acct["password"], role=role, created_by=created_by,
                                      consent=consent, preferred_language=lang)


async def seed(db: AsyncIOMotorDatabase) -> dict[str, int]:
    actor = audit.system_actor("seed-script")
    counts = {"accounts": 0, "schemes": 0, "documents": 0}

    admin = await _ensure_account(db, role="admin", **ADMIN)
    for o in OFFICERS:
        user = await _ensure_account(db, role="officer", created_by=admin["_id"],
                                     **{k: o[k] for k in ("login_id", "full_name", "password")})
        if not await db.officers.find_one({"user_id": user["_id"]}):
            await db.officers.insert_one({"_id": user["_id"], "user_id": user["_id"], "region": o["region"],
                                          "assigned_schemes": o["assigned_schemes"], "created_by": admin["_id"],
                                          "created_at": user["created_at"]})
    for u in DEMO_USERS:
        consent = {"accepted_at": datetime.now(UTC), "mode": "save_profile", "version": users.CONSENT_VERSION, "seeded": True}
        await _ensure_account(db, role="user", consent=consent, lang=u["lang"],
                              **{k: u[k] for k in ("login_id", "full_name", "password")})
    counts["accounts"] = await db.users.count_documents({})

    scheme_rows, catalog = schemes.load_seed_files()
    for doc_id, entry in catalog.items():
        await db.required_documents.update_one({"_id": doc_id}, {"$set": entry}, upsert=True)
    counts["documents"] = len(catalog)

    # Insert schemes without exclusions first, then set exclusions, so references always resolve.
    for row in scheme_rows:
        model = SchemeIn.model_validate(row)  # validates rules and fields
        if await db.schemes.find_one({"slug": model.slug}, {"_id": 1}):
            continue
        data = model.model_dump(mode="json")
        missing = [d for d in data["documents"] if d not in catalog]
        if missing:
            raise ValueError(f"{model.slug}: unknown documents {missing}")
        await schemes.insert_scheme(db, data, actor, note="seed", last_verified=SEED_COMPILED_ON,
                                    data_status="sample")
        counts["schemes"] += 1
    slugs = {r["slug"] for r in scheme_rows}
    for row in scheme_rows:
        bad = [s for s in row.get("exclusive_with", []) if s not in slugs]
        if bad:
            raise ValueError(f"{row['slug']}: exclusive_with refers to unknown schemes {bad}")

    if counts["schemes"]:
        await audit.record(db, actor=actor, action="seed.run", after=dict(counts))
    log.info("seed complete: %s", counts)
    return counts
