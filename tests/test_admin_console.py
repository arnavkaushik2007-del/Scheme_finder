"""Step 7: admin console — 2FA, officers, users, fake-account detection, database browser, rules review, analytics."""

from __future__ import annotations

import json

import pytest

from app.security import totp
from app.seed import seed
from scripts.make_sample_docs import DOCS, make_pdf
from tests.conftest import ADMIN, OFFICER, USER, bearer, login


@pytest.fixture(autouse=True)
def _files(settings, tmp_path):
    settings.file_storage_dir = str(tmp_path / "files")

# ----------------------------------------------------------------------------- 2FA


def test_totp_rfc6238_vector():
    # RFC 6238 test secret "12345678901234567890" (base32) at T=59 -> 287082 (SHA-1, 6 digits)
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp.now_code(secret, at=59) == "287082"
    assert totp.verify(secret, "287082", at=59) and not totp.verify(secret, "000000", at=59)


async def test_two_factor_login_for_admin(client, tokens):
    h = bearer(tokens["admin"])
    setup = (await client.post("/api/auth/2fa/setup", headers=h)).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/")
    assert (await client.post("/api/auth/2fa/enable", headers=h, json={"code": "000000"})).status_code == 422
    r = await client.post("/api/auth/2fa/enable", headers=h, json={"code": totp.now_code(setup["secret"])})
    assert r.status_code == 204
    body = {"login_id": ADMIN["login_id"], "password": ADMIN["password"], "portal": "admin"}
    r = await client.post("/api/auth/login", json=body)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "otp_required"
    assert (await client.post("/api/auth/login", json={**body, "otp": "123456"})).status_code == 401
    ok = await client.post("/api/auth/login", json={**body, "otp": totp.now_code(setup["secret"])})
    assert ok.status_code == 200
    me = (await client.get("/api/auth/me", headers=bearer(ok.json()))).json()
    assert me["totp_enabled"] is True


async def test_citizens_cannot_set_up_2fa(client, tokens):
    assert (await client.post("/api/auth/2fa/setup", headers=bearer(tokens["user"]))).status_code == 403


async def test_secret_stored_encrypted(client, db, tokens):
    setup = (await client.post("/api/auth/2fa/setup", headers=bearer(tokens["officer"]))).json()
    doc = await db.users.find_one({"login_id": OFFICER["login_id"]})
    assert setup["secret"] not in json.dumps(doc, default=str)

# ----------------------------------------------------------------------------- officers


@pytest.fixture
async def seeded(db):
    await seed(db)
    return db


async def _officer_id(db, login_id="officer.patna@schemefinder.test"):
    return (await db.users.find_one({"login_id": login_id}))["_id"]


async def test_update_officer_assignment(client, seeded, tokens):
    oid = await _officer_id(seeded)
    r = await client.patch(f"/api/admin/officers/{oid}", headers=bearer(tokens["admin"]),
                           json={"region": "Jharkhand", "assigned_schemes": ["ignoaps"], "reason": "transfer order 12"})
    assert r.status_code == 200 and r.json()["region"] == "Jharkhand" and r.json()["assigned_schemes"] == ["ignoaps"]
    e = await seeded.audit_log.find_one({"action": "officer.update"})
    assert e["before"]["region"] == "Bihar" and e["after"]["region"] == "Jharkhand" and e["reason"]
    r = await client.patch(f"/api/admin/officers/{oid}", headers=bearer(tokens["admin"]),
                           json={"assigned_schemes": ["nope"], "reason": "typo test"})
    assert r.status_code == 422


async def test_reset_officer_password(client, seeded, tokens):
    oid = await _officer_id(seeded)
    old = await login(client, {"login_id": "officer.patna@schemefinder.test", "password": "Review#Desk2026"}, "officer")
    r = await client.post(f"/api/admin/officers/{oid}/reset-password", headers=bearer(tokens["admin"]),
                          json={"reason": "officer forgot password"})
    temp = r.json()["temporary_password"]
    assert (await client.post("/api/auth/refresh", json={"refresh_token": old["refresh_token"]})).status_code == 401
    await login(client, {"login_id": "officer.patna@schemefinder.test", "password": temp}, "officer")
    assert await seeded.audit_log.find_one({"action": "officer.reset_password"})


async def test_delete_officer_unassigns_cases(client, seeded, tokens):
    oid = await _officer_id(seeded)
    await seeded.applications.insert_one({"_id": "a1", "assigned_officer_id": oid, "status": "under_review"})
    r = await client.delete(f"/api/admin/officers/{oid}", headers=bearer(tokens["admin"]),
                            params={"reason": "left the department"})
    assert r.json() == {"cases_unassigned": 1}
    assert (await seeded.applications.find_one({"_id": "a1"}))["assigned_officer_id"] is None
    r = await client.post("/api/auth/login", json={"login_id": "officer.patna@schemefinder.test",
                                                   "password": "Review#Desk2026", "portal": "officer"})
    assert r.status_code == 401

# ----------------------------------------------------------------------------- users + fraud


async def _citizen(client, db, n, tmp_path, *, name="Sunita Devi", dob="1958-03-12", upload=True):
    acct = {"login_id": f"fake{n}@test.in", "full_name": name, "password": "Fake!Account99", "consent_accepted": True}
    await client.post("/api/auth/register", json=acct)
    t = await login(client, acct, "user")
    from app.ai.extraction import age_from_dob

    await client.put("/api/me/profile", headers=bearer(t), json={
        "eligibility": {"age": age_from_dob(dob)}, "personal": {"full_name": name, "date_of_birth": dob}})
    if upload:
        title, lines = DOCS["aadhaar_sunita.pdf"]
        if not (tmp_path / "a.pdf").exists():  # the very same file bytes for every account
            make_pdf(tmp_path / "a.pdf", title, lines)
        await client.post("/api/documents", headers=bearer(t), data={"doc_type": "aadhaar"},
                          files={"file": ("a.pdf", (tmp_path / "a.pdf").read_bytes(), "application/pdf")})
    return t, (await db.users.find_one({"login_id": acct["login_id"]}))["_id"]


async def test_fraud_scan_flags_duplicates_with_reasons(client, seeded, tokens, tmp_path):
    _, a = await _citizen(client, seeded, 1, tmp_path)
    _, b = await _citizen(client, seeded, 2, tmp_path)
    _, c = await _citizen(client, seeded, 3, tmp_path, name="Ravi Kumar", dob="1990-01-01", upload=False)
    for i in range(4, 10):  # enough honest accounts for the anomaly model to run
        await _citizen(client, seeded, i, tmp_path, name=f"Person {i}", dob=f"19{70 + i}-01-01", upload=False)
    r = (await client.post("/api/admin/fraud/scan", headers=bearer(tokens["admin"]))).json()
    assert r["flagged"] >= 2 and r["anomaly_model"] is True
    users = (await client.get("/api/admin/users", headers=bearer(tokens["admin"]), params={"flagged": True})).json()
    flagged = {u["id"]: u for u in users}
    assert a in flagged and b in flagged and c not in flagged
    codes = {x["code"] for x in flagged[a]["flag"]["reasons"]}
    assert {"duplicate_file", "shared_identity"} <= codes
    assert "@" in flagged[a]["login_id"] and "•" in flagged[a]["login_id"]  # masked
    # admin clears one after checking; the other is deleted as fake
    assert (await client.post(f"/api/admin/fraud/{a}/clear", headers=bearer(tokens["admin"]),
                              json={"reason": "verified in person"})).status_code == 204
    assert (await client.delete(f"/api/admin/users/{b}", headers=bearer(tokens["admin"]),
                                params={"reason": "duplicate fake account"})).status_code == 204
    gone = await seeded.users.find_one({"_id": b})
    assert gone["deleted_at"] and gone["full_name"] == "" and await seeded.documents.count_documents(
        {"user_id": b, "deleted_at": None}) == 0
    assert await seeded.audit_log.find_one({"action": "user.delete", "target.id": b})


async def test_suspend_user_blocks_access(client, seeded, tokens):
    uid = (await seeded.users.find_one({"login_id": USER["login_id"]}))["_id"]
    r = await client.patch(f"/api/admin/users/{uid}/status", headers=bearer(tokens["admin"]),
                           json={"status": "suspended", "reason": "suspicious activity"})
    assert r.json()["status"] == "suspended"
    assert (await client.get("/api/auth/me", headers=bearer(tokens["user"]))).status_code == 403

# ----------------------------------------------------------------------------- database browser


async def test_database_browser_masks_and_logs_reveals(client, seeded, tokens):
    h = bearer(tokens["admin"])
    page = (await client.get("/api/admin/database/users", headers=h, params={"limit": 50})).json()
    citizen = next(r for r in page["rows"] if r.get("role") == "user" and r["login_id"].startswith("su")
                   and r["login_id"].endswith("@demo.test"))
    assert citizen["password_hash"] == "[secret — never shown]"
    assert "•" in citizen["login_id"] and "•" in citizen["full_name"]
    r = await client.post(f"/api/admin/database/users/{citizen['_id']}/reveal", headers=h,
                          json={"reason": "check a complaint"})
    full = r.json()
    assert full["login_id"] == "sunita@demo.test" and full["password_hash"] == "[secret — never shown]"
    e = await seeded.audit_log.find_one({"action": "data.reveal"})
    assert e["reason"] == "check a complaint" and e["target"]["id"] == citizen["_id"]
    assert (await client.post(f"/api/admin/database/users/{citizen['_id']}/reveal", headers=h,
                              json={"reason": "x"})).status_code == 422  # a real reason is required
    assert (await client.get("/api/admin/database/refresh_tokens", headers=h)).status_code == 404
    assert (await client.get("/api/admin/database/users", headers=bearer(tokens["officer"]))).status_code == 403

# ----------------------------------------------------------------------------- rules review


GUIDELINE = ("Eligibility. The scheme is open to women aged 21 to 60 years who are residents of Madhya Pradesh. "
             "The annual family income must not exceed Rs 2.5 lakh. Income tax payers are not eligible. "
             "Applicants must hold a savings bank account.")


async def test_rule_ingestion_and_review(client, seeded, tokens):
    h = bearer(tokens["admin"])
    r = await client.post("/api/admin/rules/ingest", headers=h, data={"scheme": "ladli-behna-mp", "text": GUIDELINE})
    assert r.status_code == 201, r.text
    p = r.json()
    leaves = {x["field"]: x for x in p["proposed_rules"]["all"]}
    assert leaves["age"]["value"] == [21, 60] and leaves["annual_income"]["value"] == 250000
    assert leaves["gender"]["value"] == "female" and leaves["state"]["value"] == "Madhya Pradesh"
    assert leaves["is_income_tax_payer"]["op"] == "is_false" and leaves["has_bank_account"]["op"] == "is_true"
    assert all(q in GUIDELINE.replace("  ", " ") or q.split(".")[0] in GUIDELINE for q in p["evidence"].values())
    assert p["status"] == "pending" and p["method"] == "pattern"
    # nothing is live until approved
    assert (await client.get("/api/schemes/ladli-behna-mp")).json()["version"] == 1
    bad = {"all": [{"id": "x", "field": "salary", "op": "eq", "value": 1}]}
    r = await client.post(f"/api/admin/rules/proposals/{p['id']}/decision", headers=h,
                          json={"approve": True, "reason": "checked against PDF", "rules": bad})
    assert r.status_code == 422
    r = await client.post(f"/api/admin/rules/proposals/{p['id']}/decision", headers=h,
                          json={"approve": True, "reason": "checked against the 2026 guideline"})
    assert r.json()["status"] == "approved" and r.json()["new_version"] == 2
    assert (await client.get("/api/schemes/ladli-behna-mp")).json()["version"] == 2
    assert await seeded.audit_log.find_one({"action": "rules.approved"})


async def test_rule_ingestion_pdf_and_reject(client, seeded, tokens, tmp_path):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_textbox(pymupdf.Rect(40, 40, 560, 800), GUIDELINE, fontsize=11)
    pdf = doc.tobytes()
    h = bearer(tokens["admin"])
    r = await client.post("/api/admin/rules/ingest", headers=h, data={"scheme": "ladli-behna-mp"},
                          files={"file": ("guideline.pdf", pdf, "application/pdf")})
    assert r.status_code == 201 and r.json()["source"]["filename"] == "guideline.pdf"
    pid = r.json()["id"]
    r = await client.post(f"/api/admin/rules/proposals/{pid}/decision", headers=h,
                          json={"approve": False, "reason": "outdated circular"})
    assert r.json()["status"] == "rejected"
    assert (await client.get("/api/admin/rules/proposals", headers=h, params={"status": "pending"})).json() == []
    assert (await client.post("/api/admin/rules/ingest", headers=h,
                              data={"scheme": "ladli-behna-mp", "text": "Nothing useful in this text at all here."})
            ).status_code == 422


async def test_audit_filters_and_analytics(client, seeded, tokens):
    h = bearer(tokens["admin"])
    oid = await _officer_id(seeded)
    await client.patch(f"/api/admin/officers/{oid}", headers=h, json={"region": "Bihar", "reason": "no change test"})
    r = (await client.get("/api/admin/audit", headers=h, params={"action": "officer."})).json()
    assert r["entries"] and all(e["action"].startswith("officer.") for e in r["entries"]) and r["chain_ok"]
    a = (await client.get("/api/admin/analytics", headers=h)).json()
    assert {"applications_by_scheme", "status_counts", "ai", "officers", "assistant", "users"} <= set(a)
