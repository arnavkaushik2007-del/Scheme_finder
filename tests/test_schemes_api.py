"""Scheme catalogue, matching and versioned admin edits (with audit)."""

from __future__ import annotations

import copy

import pytest

from app.seed import seed
from app.services.schemes import load_seed_files
from tests.conftest import bearer


@pytest.fixture
async def seeded(db):
    return await seed(db)


async def test_seed_counts_and_idempotent(db):
    first = await seed(db)
    assert first["schemes"] == 30
    second = await seed(db)
    assert second["schemes"] == 0
    assert await db.users.count_documents({"role": "user"}) == 3
    assert await db.users.count_documents({"role": "officer"}) == 2
    assert await db.users.count_documents({"role": "admin"}) == 1
    assert await db.scheme_versions.count_documents({}) == 30


async def test_public_catalogue(client, seeded):
    r = await client.get("/api/schemes")
    assert r.status_code == 200 and len(r.json()) == 30
    assert "rules" not in r.json()[0]
    stats = (await client.get("/api/schemes/stats")).json()
    assert stats == {"schemes": 30, "last_verified": "2026-11-07"}


async def test_filters_and_search(client, seeded):
    pensions = (await client.get("/api/schemes", params={"category": "pension"})).json()
    assert pensions and all(s["category"] == "pension" for s in pensions)
    states = (await client.get("/api/schemes", params={"level": "state"})).json()
    assert len(states) == 3
    hits = (await client.get("/api/schemes", params={"q": "pension"})).json()
    assert any(s["slug"] == "atal-pension-yojana" for s in hits)
    hindi = (await client.get("/api/schemes", params={"q": "पेंशन"})).json()
    assert len(hindi) >= 3
    assert (await client.get("/api/schemes", params={"category": "bogus"})).status_code == 422


async def test_scheme_detail(client, seeded):
    d = (await client.get("/api/schemes/ignoaps")).json()
    assert d["version"] == 1 and d["documents_detail"][0]["name"]["hi"]
    assert d["rule_list"] and d["rule_list"][0]["text"]["en"]
    assert {e["slug"] for e in d["exclusive_with_detail"]} == {"igndps", "ignwps"}
    assert (await client.get("/api/schemes/nope")).status_code == 404


async def test_match_ranks_and_explains(client, seeded, tokens):
    profile = {"age": 67, "gender": "female", "is_bpl": True, "marital_status": "widowed", "annual_income": 60000,
               "residence": "rural", "has_bank_account": True, "is_income_tax_payer": False, "state": "Bihar"}
    r = await client.post("/api/match", json={"profile": profile}, headers=bearer(tokens["user"]))
    assert r.status_code == 200
    data = r.json()
    statuses = [i["result"]["status"] for i in data["items"]]
    order = {"eligible": 2, "maybe": 1, "not_eligible": 0}
    assert statuses == sorted(statuses, key=lambda s: order[s], reverse=True)
    by_slug = {i["scheme"]["slug"]: i["result"] for i in data["items"]}
    assert by_slug["ignoaps"]["status"] == "eligible"
    assert by_slug["atal-pension-yojana"]["status"] == "not_eligible"
    # other states' schemes are left out entirely for a Bihar resident
    assert "ladli-behna-mp" not in by_slug
    assert data["counts"]["eligible"] >= 1


async def test_match_validates_profile(client, seeded, tokens):
    r = await client.post("/api/match", json={"profile": {"age": -4}}, headers=bearer(tokens["user"]))
    assert r.status_code == 422
    r = await client.post("/api/match", json={"profile": {"aadhaar": "123412341234"}}, headers=bearer(tokens["user"]))
    assert r.status_code == 422


async def test_match_does_not_store_profile(client, seeded, tokens, db):
    before = await db.list_collection_names()
    await client.post("/api/match", json={"profile": {"age": 30}}, headers=bearer(tokens["user"]))
    assert set(await db.list_collection_names()) == set(before)


def _payload(slug="atal-pension-yojana"):
    row = copy.deepcopy(next(r for r in load_seed_files()[0] if r["slug"] == slug))
    return row


async def test_admin_update_creates_new_version_and_audit(client, seeded, tokens, db):
    body = _payload()
    body["rules"]["all"][0] = {"id": "age", "field": "age", "op": "between", "value": [18, 45]}
    body["change_note"] = "Test: age limit widened"
    r = await client.put("/api/admin/schemes/atal-pension-yojana", json=body, headers=bearer(tokens["admin"]))
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    versions = (await client.get("/api/admin/schemes/atal-pension-yojana/versions",
                                 headers=bearer(tokens["admin"]))).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[1]["data"]["rules"]["all"][0]["value"] != [18, 45]  # old version kept intact
    entry = await db.audit_log.find_one({"action": "scheme.update"})
    assert entry["reason"] == "Test: age limit widened" and entry["before"]["version"] == 1
    assert "rules" in entry["after"]


async def test_admin_update_rejects_invalid_rules(client, seeded, tokens):
    body = _payload()
    body["rules"] = {"all": [{"id": "x", "field": "salary", "op": "eq", "value": 1}]}
    body["change_note"] = "bad"
    r = await client.put("/api/admin/schemes/atal-pension-yojana", json=body, headers=bearer(tokens["admin"]))
    assert r.status_code == 422


async def test_admin_update_rejects_unknown_document(client, seeded, tokens):
    body = _payload()
    body["documents"] = ["aadhaar", "unicorn_licence"]
    body["change_note"] = "bad doc"
    r = await client.put("/api/admin/schemes/atal-pension-yojana", json=body, headers=bearer(tokens["admin"]))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_documents"


async def test_officer_cannot_edit_schemes(client, seeded, tokens):
    body = {**_payload(), "change_note": "officer try"}
    r = await client.put("/api/admin/schemes/atal-pension-yojana", json=body, headers=bearer(tokens["officer"]))
    assert r.status_code == 403


async def test_admin_create_and_soft_delete(client, seeded, tokens, db):
    body = _payload()
    body["slug"] = "test-scheme"
    body["exclusive_with"] = []
    r = await client.post("/api/admin/schemes", json=body, headers=bearer(tokens["admin"]))
    assert r.status_code == 201, r.text
    assert (await client.post("/api/admin/schemes", json=body, headers=bearer(tokens["admin"]))).status_code == 409
    r = await client.delete("/api/admin/schemes/test-scheme", params={"reason": "duplicate entry"},
                            headers=bearer(tokens["admin"]))
    assert r.status_code == 204
    assert (await client.get("/api/schemes/test-scheme")).status_code == 404
    assert await db.schemes.find_one({"slug": "test-scheme", "deleted_at": {"$ne": None}})  # soft delete
    assert await db.audit_log.find_one({"action": "scheme.delete", "reason": "duplicate entry"})


async def test_check_single_scheme(client, seeded, tokens):
    r = await client.post("/api/schemes/ignoaps/check", json={"age": 70, "is_bpl": True},
                          headers=bearer(tokens["officer"]))
    assert r.status_code == 200 and r.json()["status"] == "eligible" and r.json()["version"] == 1
