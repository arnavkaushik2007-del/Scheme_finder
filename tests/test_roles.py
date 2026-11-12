"""Server-side role checks: every protected endpoint refuses the wrong role."""

from __future__ import annotations

import pytest

from app.services import audit
from tests.conftest import bearer

NEW_OFFICER = {"login_id": "fresh.officer@test.in", "full_name": "Fresh Officer", "password": "Fr3sh!Officer",
               "region": "Bihar"}

ADMIN_ENDPOINTS = [
    ("GET", "/api/admin/officers", None),
    ("POST", "/api/admin/officers", NEW_OFFICER),
    ("GET", "/api/admin/audit", None),
    ("GET", "/api/admin/stats", None),
    ("DELETE", "/api/admin/schemes/anything?reason=test", None),
]


@pytest.mark.parametrize("method,path,body", ADMIN_ENDPOINTS)
async def test_admin_endpoints_refuse_user_and_officer(client, tokens, method, path, body):
    for role in ("user", "officer"):
        r = await client.request(method, path, json=body, headers=bearer(tokens[role]))
        assert r.status_code == 403, (role, path, r.text)
        assert r.json()["detail"]["code"] == "forbidden_role"


@pytest.mark.parametrize("method,path,body", ADMIN_ENDPOINTS)
async def test_admin_endpoints_refuse_anonymous(client, method, path, body):
    r = await client.request(method, path, json=body)
    assert r.status_code == 401


async def test_match_is_for_citizens_only(client, tokens):
    body = {"profile": {"age": 30}}
    assert (await client.post("/api/match", json=body)).status_code == 401
    for role in ("officer", "admin"):
        assert (await client.post("/api/match", json=body, headers=bearer(tokens[role]))).status_code == 403
    assert (await client.post("/api/match", json=body, headers=bearer(tokens["user"]))).status_code == 200


async def test_admin_creates_officer_who_can_then_log_in(client, tokens, db):
    r = await client.post("/api/admin/officers", json=NEW_OFFICER, headers=bearer(tokens["admin"]))
    assert r.status_code == 201, r.text
    officer_id = r.json()["id"]
    r = await client.post("/api/auth/login", json={"login_id": NEW_OFFICER["login_id"],
                                                   "password": NEW_OFFICER["password"], "portal": "officer"})
    assert r.status_code == 200 and r.json()["role"] == "officer"
    entry = await db.audit_log.find_one({"action": "officer.create"})
    assert entry["target"]["id"] == officer_id and entry["actor"]["role"] == "admin"


async def test_admin_deactivates_officer(client, tokens, accounts):
    oid = accounts["officer"]["_id"]
    r = await client.patch(f"/api/admin/officers/{oid}/status", json={"status": "suspended", "reason": "left post"},
                           headers=bearer(tokens["admin"]))
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    # the officer's existing access token stops working at once
    assert (await client.get("/api/auth/me", headers=bearer(tokens["officer"]))).status_code == 403


async def test_admin_cannot_deactivate_a_citizen_through_officer_route(client, tokens, accounts):
    uid = accounts["user"]["_id"]
    r = await client.patch(f"/api/admin/officers/{uid}/status", json={"status": "suspended", "reason": "nope"},
                           headers=bearer(tokens["admin"]))
    assert r.status_code == 404


async def test_audit_chain_is_tamper_evident(client, tokens, db):
    await client.post("/api/admin/officers", json=NEW_OFFICER, headers=bearer(tokens["admin"]))
    ok, bad = await audit.verify_chain(db)
    assert ok and bad is None
    first = await db.audit_log.find_one({"seq": 1})
    await db.audit_log.update_one({"_id": first["_id"]}, {"$set": {"action": "edited"}})
    ok, bad = await audit.verify_chain(db)
    assert not ok and bad == 1
    r = await client.get("/api/admin/audit", headers=bearer(tokens["admin"]))
    assert r.json()["chain_ok"] is False


async def test_no_audit_mutation_routes_exist(client):
    from app.main import create_api

    paths = {(m, r.path) for r in create_api().routes for m in getattr(r, "methods", set())}
    assert not any("audit" in p and m in {"PUT", "PATCH", "DELETE"} for m, p in paths)
