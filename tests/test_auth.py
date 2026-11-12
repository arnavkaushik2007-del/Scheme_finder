from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.security import passwords
from app.services import users
from tests.conftest import ADMIN, OFFICER, USER, bearer, login

GOOD = {"login_id": "new.person@test.in", "full_name": "New Person", "password": "Str0ng!Passw0rd",
        "consent_accepted": True}


async def test_register_and_login(client):
    r = await client.post("/api/auth/register", json=GOOD)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "user" and body["consent"]["mode"] == "save_profile"
    assert "password" not in str(body)
    tokens = await login(client, GOOD, "user")
    me = await client.get("/api/auth/me", headers=bearer(tokens))
    assert me.status_code == 200 and me.json()["login_id"] == "new.person@test.in"


async def test_password_is_hashed_with_argon2(client, db):
    await client.post("/api/auth/register", json=GOOD)
    doc = await db.users.find_one({"login_id": GOOD["login_id"]})
    assert doc["password_hash"].startswith("$argon2id$")
    assert GOOD["password"] not in str(doc)


async def test_register_rejects_weak_password(client):
    r = await client.post("/api/auth/register", json={**GOOD, "password": "short"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "weak_password"
    assert "too_short" in r.json()["detail"]["problems"]


async def test_register_requires_consent(client):
    r = await client.post("/api/auth/register", json={**GOOD, "consent_accepted": False})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "consent_required"


async def test_register_duplicate(client):
    assert (await client.post("/api/auth/register", json=GOOD)).status_code == 201
    r = await client.post("/api/auth/register", json={**GOOD, "login_id": "NEW.person@test.in"})
    assert r.status_code == 409


async def test_register_cannot_choose_role(client):
    r = await client.post("/api/auth/register", json={**GOOD, "role": "admin"})
    assert r.status_code == 422  # extra fields are forbidden


def test_password_policy_rules():
    assert passwords.password_problems("Str0ng!Passw0rd") == []
    assert "no_symbol" in passwords.password_problems("Str0ngPassw0rd")
    assert "no_upper" in passwords.password_problems("str0ng!passw0rd")
    assert "contains_login_id" in passwords.password_problems("Ramesh!2026xx", "ramesh@x.in")


async def test_wrong_password(client, accounts):
    r = await client.post("/api/auth/login", json={"login_id": USER["login_id"], "password": "Wrong!Pass123",
                                                   "portal": "user"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "invalid_credentials"


async def test_unknown_user_same_error(client):
    r = await client.post("/api/auth/login", json={"login_id": "ghost@test.in", "password": "Wrong!Pass123"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "invalid_credentials"


async def test_each_role_only_logs_in_through_its_own_portal(client, accounts):
    for acct, wrong in ((USER, "admin"), (USER, "officer"), (OFFICER, "user"), (OFFICER, "admin"),
                        (ADMIN, "user"), (ADMIN, "officer")):
        r = await client.post("/api/auth/login", json={"login_id": acct["login_id"], "password": acct["password"],
                                                       "portal": wrong})
        assert r.status_code == 401, (acct["login_id"], wrong)
    for acct, portal in ((USER, "user"), (OFFICER, "officer"), (ADMIN, "admin")):
        assert (await login(client, acct, portal))["role"] == portal


async def test_lockout_after_repeated_failures(client, accounts, settings):
    bad = {"login_id": USER["login_id"], "password": "Wrong!Pass123", "portal": "user"}
    codes = [(await client.post("/api/auth/login", json=bad)).status_code for _ in range(settings.login_max_failures)]
    assert codes[:-1] == [401] * (settings.login_max_failures - 1) and codes[-1] == 423
    # even the correct password is refused while locked
    r = await client.post("/api/auth/login", json={**bad, "password": USER["password"]})
    assert r.status_code == 423 and r.json()["detail"]["code"] == "account_locked"


async def test_lockout_expires(client, db, accounts, settings):
    await db.users.update_one({"login_id": USER["login_id"]},
                              {"$set": {"locked_until": datetime.now(UTC) - timedelta(seconds=1)}})
    assert (await login(client, USER, "user"))["role"] == "user"


async def test_rate_limit_on_login(client, accounts, settings, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 3)
    bad = {"login_id": "nobody@test.in", "password": "x", "portal": "user"}
    codes = [(await client.post("/api/auth/login", json=bad)).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]


async def test_refresh_rotation_and_reuse_detection(client, accounts):
    t1 = await login(client, USER, "user")
    r = await client.post("/api/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    assert r.status_code == 200
    t2 = r.json()
    assert t2["refresh_token"] != t1["refresh_token"]
    # replaying the old token fails and revokes the whole family
    assert (await client.post("/api/auth/refresh", json={"refresh_token": t1["refresh_token"]})).status_code == 401
    assert (await client.post("/api/auth/refresh", json={"refresh_token": t2["refresh_token"]})).status_code == 401


async def test_logout_revokes_refresh(client, accounts):
    t = await login(client, USER, "user")
    assert (await client.post("/api/auth/logout", json={"refresh_token": t["refresh_token"]})).status_code == 204
    assert (await client.post("/api/auth/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 401


async def test_access_token_cannot_be_used_as_refresh(client, accounts):
    t = await login(client, USER, "user")
    assert (await client.post("/api/auth/refresh", json={"refresh_token": t["access_token"]})).status_code == 401
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {t['refresh_token']}"})
    assert r.status_code == 401


async def test_tampered_token_rejected(client, accounts):
    t = await login(client, USER, "user")
    forged = t["access_token"][:-4] + ("AAAA" if not t["access_token"].endswith("AAAA") else "BBBB")
    assert (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})).status_code == 401


async def test_suspended_account_blocked_immediately(client, db, accounts):
    t = await login(client, USER, "user")
    await db.users.update_one({"login_id": USER["login_id"]}, {"$set": {"status": "suspended"}})
    assert (await client.get("/api/auth/me", headers=bearer(t))).status_code == 403
    r = await client.post("/api/auth/login", json={**{k: USER[k] for k in ("login_id", "password")}, "portal": "user"})
    assert r.status_code == 403


async def test_change_password_revokes_sessions(client, accounts):
    t = await login(client, USER, "user")
    r = await client.post("/api/auth/change-password", headers=bearer(t),
                          json={"current_password": USER["password"], "new_password": "N3w!Password99"})
    assert r.status_code == 204
    assert (await client.post("/api/auth/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 401
    await login(client, {**USER, "password": "N3w!Password99"}, "user")


async def test_password_reset_flow(client, accounts):
    r = await client.post("/api/auth/password-reset/request", json={"login_id": USER["login_id"]})
    token = r.json()["dev_reset_token"]
    r = await client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": "R3set!Password"})
    assert r.status_code == 204
    await login(client, {**USER, "password": "R3set!Password"}, "user")
    # single use
    r = await client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": "R3set!Again99"})
    assert r.status_code == 400


async def test_password_reset_does_not_reveal_accounts(client, accounts):
    a = await client.post("/api/auth/password-reset/request", json={"login_id": "ghost@test.in"})
    b = await client.post("/api/auth/password-reset/request", json={"login_id": OFFICER["login_id"]})
    assert a.json() == b.json() == {"message": "If the account exists, reset instructions have been issued."}


async def test_staff_login_is_audited(client, db, accounts):
    await login(client, ADMIN, "admin")
    entry = await db.audit_log.find_one({"action": "auth.login"})
    assert entry and entry["actor"]["role"] == "admin"


async def test_authenticate_rehash_keeps_login_working(db, settings, accounts):
    user = await users.authenticate(db, settings, USER["login_id"], USER["password"], "user")
    assert user["role"] == "user"
