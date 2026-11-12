"""Append-only, hash-chained audit log of privileged actions.

There is no update or delete path for this collection anywhere in the code. Each entry stores
the SHA-256 of the previous entry, so editing or removing a row directly in MongoDB breaks
the chain and ``verify_chain`` reports where.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

GENESIS = "0" * 64
_lock = asyncio.Lock()


def _digest(entry: dict[str, Any]) -> str:
    body = {k: entry[k] for k in ("seq", "ts", "actor", "action", "target", "before", "after", "reason", "prev_hash")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


async def record(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any],
    action: str,
    target: dict[str, Any] | None = None,
    before: Any = None,
    after: Any = None,
    reason: str | None = None,
) -> dict[str, Any]:
    async with _lock:
        for _ in range(5):
            last = await db.audit_log.find_one(sort=[("seq", -1)])
            entry = {
                "_id": uuid.uuid4().hex,
                "seq": (last["seq"] + 1) if last else 1,
                "ts": datetime.now(UTC).isoformat(),
                "actor": actor,
                "action": action,
                "target": target or {},
                "before": before,
                "after": after,
                "reason": reason,
                "prev_hash": last["hash"] if last else GENESIS,
            }
            entry["hash"] = _digest(entry)
            try:
                await db.audit_log.insert_one(entry)
                return entry
            except DuplicateKeyError:  # another process wrote the same seq; retry
                continue
    raise RuntimeError("could not append audit entry")


async def verify_chain(db: AsyncIOMotorDatabase) -> tuple[bool, int | None]:
    """Returns (ok, first_bad_seq)."""
    prev = GENESIS
    expected_seq = 1
    async for entry in db.audit_log.find().sort("seq", 1):
        if entry["seq"] != expected_seq or entry["prev_hash"] != prev or _digest(entry) != entry["hash"]:
            return False, entry["seq"]
        prev = entry["hash"]
        expected_seq += 1
    return True, None


def system_actor(name: str = "system") -> dict[str, Any]:
    return {"id": name, "role": "system", "login_id": name, "ip": None}
