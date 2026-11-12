"""MongoDB access through Motor.

A single client is created on start-up. ``MONGO_URL=mongomock://`` swaps in an in-memory
database (mongomock-motor) so the app and the tests can run without a MongoDB server.
"""

from __future__ import annotations

import logging
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from app.config import Settings

log = logging.getLogger(__name__)

_client: Any = None
_db: Any = None


def connect(settings: Settings) -> AsyncIOMotorDatabase:
    global _client, _db
    if settings.uses_mock_db:
        from mongomock_motor import AsyncMongoMockClient

        log.warning("Using IN-MEMORY mongomock database: data is lost when the app stops")
        _client = AsyncMongoMockClient()
    else:
        _client = AsyncIOMotorClient(settings.mongo_url, uuidRepresentation="standard", tz_aware=True)
    _db = _client[settings.mongo_db]
    return _db


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database not connected")
    return _db


def close() -> None:
    global _client, _db
    if _client is not None and hasattr(_client, "close"):
        _client.close()
    _client, _db = None, None


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.users.create_index([("login_id", ASCENDING)], unique=True)
    await db.users.create_index([("role", ASCENDING), ("status", ASCENDING)])
    await db.officers.create_index([("user_id", ASCENDING)], unique=True)
    await db.refresh_tokens.create_index([("jti", ASCENDING)], unique=True)
    await db.refresh_tokens.create_index([("user_id", ASCENDING)])
    await db.password_resets.create_index([("token_hash", ASCENDING)], unique=True)
    await db.schemes.create_index([("slug", ASCENDING)], unique=True)
    await db.schemes.create_index([("category", ASCENDING), ("level", ASCENDING)])
    await db.scheme_versions.create_index([("scheme_id", ASCENDING), ("version", DESCENDING)], unique=True)
    await db.audit_log.create_index([("seq", ASCENDING)], unique=True)
    await db.audit_log.create_index([("ts", DESCENDING)])
    await db.profiles.create_index([("user_id", ASCENDING)], unique=True)
    await db.documents.create_index([("user_id", ASCENDING), ("deleted_at", ASCENDING)])
    await db.documents.create_index([("file_hash", ASCENDING)])
    await db.chat_logs.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    await db.chat_logs.create_index([("abstained", ASCENDING)])
    await db.applications.create_index([("user_id", ASCENDING), ("scheme_slug", ASCENDING), ("status", ASCENDING)])
    await db.applications.create_index([("assigned_officer_id", ASCENDING), ("status", ASCENDING)])
    await db.notifications.create_index([("user_id", ASCENDING), ("read", ASCENDING), ("seq", DESCENDING)])
    await db.decisions.create_index([("officer_id", ASCENDING), ("at", DESCENDING)])
    await db.decisions.create_index([("application_id", ASCENDING)])
    await db.ai_reports.create_index([("application_id", ASCENDING), ("created_at", DESCENDING)])
    log.info("MongoDB indexes ensured")
