from __future__ import annotations

import os

os.environ.update({
    "APP_ENV": "test",
    "MONGO_URL": "mongomock://",
    "MONGO_DB": "scheme_finder_test",
    "JWT_SECRET": "test-secret-0123456789-abcdefghijklmnopqrstuvwxyz",
    "STORAGE_SECRET": "test-storage-secret-0123456789",
    "SEED_ON_STARTUP": "false",
    "RATE_LIMIT_AUTH_PER_MINUTE": "1000",
    "EMBEDDING_BACKEND": "hash",
    "VECTOR_BACKEND": "memory",
    "LLM_PROVIDER": "mock",
    "ENABLE_VOICE": "false",
})

from collections.abc import AsyncIterator  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import db as database  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import create_api  # noqa: E402
from app.security.rate_limit import limiter  # noqa: E402
from app.services import users  # noqa: E402

ADMIN = {"login_id": "root@test.in", "full_name": "Test Admin", "password": "Adm1n!Password"}
OFFICER = {"login_id": "off@test.in", "full_name": "Test Officer", "password": "0fficer!Password"}
USER = {"login_id": "citizen@test.in", "full_name": "Test Citizen", "password": "C1tizen!Password"}


@pytest.fixture
def settings():
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
async def db(settings):
    from app.ai import embeddings, index, llm, vectorstore

    limiter.reset()
    index.STATE.update(ready=False, building=False, chunks=0)
    vectorstore.set_store(None)
    embeddings.set_embedder(None)
    llm.set_llm(None)
    d = database.connect(settings)
    await database.ensure_indexes(d)
    yield d
    database.close()


@pytest.fixture
async def client(db) -> AsyncIterator[httpx.AsyncClient]:
    api = create_api()
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def accounts(db) -> dict[str, dict[str, Any]]:
    out = {}
    for role, acct in (("admin", ADMIN), ("officer", OFFICER), ("user", USER)):
        out[role] = await users.create_account(db, role=role, **acct)
    return out


async def login(client: httpx.AsyncClient, acct: dict[str, str], portal: str) -> dict[str, Any]:
    r = await client.post("/api/auth/login", json={"login_id": acct["login_id"], "password": acct["password"],
                                                   "portal": portal})
    assert r.status_code == 200, r.text
    return r.json()


def bearer(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
async def tokens(client, accounts) -> dict[str, dict[str, Any]]:
    return {
        "admin": await login(client, ADMIN, "admin"),
        "officer": await login(client, OFFICER, "officer"),
        "user": await login(client, USER, "user"),
    }
