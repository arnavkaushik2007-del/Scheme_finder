"""Step 5: application form, pre-check, submission, assignment, lifecycle and notifications."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.seed import seed
from app.services import applications as apps
from app.services import precheck
from app.services.errors import ServiceError
from scripts.make_sample_docs import DOCS, make_pdf
from tests.conftest import USER, bearer, login

FORM = {"eligibility": {"age": 67, "is_bpl": True, "state": "Bihar", "gender": "female"},
        "personal": {"full_name": "Sunita Devi", "date_of_birth": "1958-03-12", "aadhaar_last4": "8901"}}


@pytest.fixture(autouse=True)
def _files(settings, tmp_path):
    settings.file_storage_dir = str(tmp_path / "files")


@pytest.fixture
async def env(client, db, accounts, tmp_path):
    await seed(db)
    await db.users.update_one({"login_id": USER["login_id"]},
                              {"$set": {"consent": {"mode": "save_profile", "accepted_at": "x"}}})
    tok = await login(client, USER, "user")
    docs = {}
    for name, doc_type in (("aadhaar_sunita.pdf", "aadhaar"), ("ration_card_sunita.pdf", "ration_card")):
        title, lines = DOCS[name]
        make_pdf(tmp_path / name, title, lines)
        r = await client.post("/api/documents", headers=bearer(tok), data={"doc_type": doc_type},
                              files={"file": (name, (tmp_path / name).read_bytes(), "application/pdf")})
        docs[doc_type] = r.json()["id"]
    return {"tok": tok, "docs": docs, "db": db, "client": client, "user_id": accounts["user"]["_id"],
            "officer_id": accounts["officer"]["_id"]}


async def _draft(env, scheme="ignoaps", docs=None, form=None):
    r = await env["client"].post("/api/applications", headers=bearer(env["tok"]), json={
        "scheme": scheme, "form": form or FORM, "documents": docs if docs is not None else list(env["docs"].values())})
    assert r.status_code == 201, r.text
    return r.json()


def officer(env):
    return {"role": "officer", "id": env["officer_id"], "login_id": "off", "ip": None}


async def test_full_happy_path(env):
    c, h = env["client"], bearer(env["tok"])
    app = await _draft(env)
    assert app["status"] == "draft" and app["scheme_version"] == 1 and "assigned_officer_id" not in app
    pre = (await c.post(f"/api/applications/{app['id']}/precheck", headers=h)).json()
    assert pre["eligibility"]["status"] == "eligible"
    assert "aadhaar" not in pre["missing_documents"] and "bpl_card" not in pre["missing_documents"]  # ration card counts
    assert (await c.post(f"/api/applications/{app['id']}/submit", headers=h, json={"declaration": False})).status_code == 422
    sub = (await c.post(f"/api/applications/{app['id']}/submit", headers=h, json={"declaration": True})).json()
    assert sub["status"] == "precheck_done"
    assert [x["to"] for x in sub["history"]] == ["draft", "submitted", "precheck_done"]
    stored = await env["db"].applications.find_one({"_id": app["id"]})
    officer_profile = await env["db"].officers.find_one({"user_id": stored["assigned_officer_id"]})
    assert officer_profile["region"] == "Bihar"  # routed to the officer for the applicant's state
    env["officer_id"] = stored["assigned_officer_id"]

    db = env["db"]
    await apps.transition(db, app["id"], "under_review", actor=officer(env))
    await apps.transition(db, app["id"], "more_docs_needed", actor=officer(env), note="Need income certificate")
    r = await c.post(f"/api/applications/{app['id']}/resubmit", headers=h,
                     json={"documents": [], "note": "Applied for income certificate; attached ration card"})
    assert r.json()["status"] == "resubmitted"
    await apps.transition(db, app["id"], "under_review", actor=officer(env))
    final = await apps.transition(db, app["id"], "approved", actor=officer(env), note="All rules met")
    assert final["status"] == "approved" and final["decided_at"]
    await apps.transition(db, app["id"], "closed", actor=officer(env))
    # notifications for every step, newest first
    n = (await c.get("/api/notifications", headers=h)).json()
    assert n["unread"] >= 7 and n["items"][0]["status"] == "closed"
    assert (await c.post("/api/notifications/read", headers=h)).status_code == 204
    assert (await c.get("/api/notifications", headers=h)).json()["unread"] == 0
    # officer actions are in the audit log, citizen actions are not
    actions = [e["action"] async for e in db.audit_log.find({"target.id": app["id"]})]
    assert "application.approved" in actions and "application.submitted" not in actions


@pytest.mark.parametrize("frm,to,role", [
    ("draft", "approved", "officer"),        # cannot skip review
    ("precheck_done", "approved", "officer"),
    ("draft", "under_review", "officer"),
    ("precheck_done", "under_review", "user"),  # citizens cannot move their own case into review
    ("under_review", "approved", "user"),
    ("approved", "withdrawn", "user"),        # no withdrawing after a decision
    ("rejected", "under_review", "officer"),
])
async def test_invalid_transitions_refused(env, frm, to, role):
    app = await _draft(env)
    await env["db"].applications.update_one({"_id": app["id"]}, {"$set": {"status": frm}})
    with pytest.raises(ServiceError) as e:
        await apps.transition(env["db"], app["id"], to, actor={"role": role, "id": "x"})
    assert e.value.code == "invalid_transition"


async def test_withdraw_before_decision(env):
    c, h = env["client"], bearer(env["tok"])
    app = await _draft(env)
    await c.post(f"/api/applications/{app['id']}/submit", headers=h, json={"declaration": True})
    r = await c.post(f"/api/applications/{app['id']}/withdraw", headers=h, json={"reason": "changed my mind"})
    assert r.json()["status"] == "withdrawn"
    # a new application for the same scheme is now allowed
    await _draft(env)


async def test_one_active_application_per_scheme(env):
    await _draft(env)
    r = await env["client"].post("/api/applications", headers=bearer(env["tok"]), json={"scheme": "ignoaps"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "application_exists"


async def test_draft_edit_only_while_draft(env):
    c, h = env["client"], bearer(env["tok"])
    app = await _draft(env)
    r = await c.put(f"/api/applications/{app['id']}", headers=h, json={"form": FORM, "documents": []})
    assert r.status_code == 200 and r.json()["documents"] == []
    await c.post(f"/api/applications/{app['id']}/submit", headers=h, json={"declaration": True})
    r = await c.put(f"/api/applications/{app['id']}", headers=h, json={"form": FORM, "documents": []})
    assert r.status_code == 409


async def test_cannot_attach_someone_elses_document(env, client):
    other = {"login_id": "other@test.in", "full_name": "Other", "password": "0ther!Password", "consent_accepted": True}
    await client.post("/api/auth/register", json=other)
    t2 = await login(client, other, "user")
    r = await client.post("/api/applications", headers=bearer(t2),
                          json={"scheme": "ignoaps", "documents": [env["docs"]["aadhaar"]]})
    assert r.status_code == 404
    assert (await client.get("/api/applications", headers=bearer(t2))).json() == []
    # and cannot read or withdraw someone else's application
    app = await _draft(env)
    assert (await client.get(f"/api/applications/{app['id']}", headers=bearer(t2))).status_code == 404
    assert (await client.post(f"/api/applications/{app['id']}/withdraw", headers=bearer(t2), json={})).status_code == 404


async def test_officer_and_admin_cannot_use_citizen_endpoints(env, tokens):
    for role in ("officer", "admin"):
        assert (await env["client"].get("/api/applications", headers=bearer(tokens[role]))).status_code == 403


async def test_precheck_flags_mismatch_and_missing_documents(env):
    form = {**FORM, "personal": {**FORM["personal"], "full_name": "Kamla Prasad", "date_of_birth": "1960-01-01"}}
    app = await _draft(env, docs=[env["docs"]["aadhaar"]], form=form)
    pre = (await env["client"].post(f"/api/applications/{app['id']}/precheck", headers=bearer(env["tok"]))).json()
    codes = {f["code"] for f in pre["findings"]}
    assert {"name_mismatch", "dob_mismatch", "unreviewed_document"} <= codes
    assert "bpl_card" in pre["missing_documents"]
    assert pre["suggested"] == "needs_attention" and pre["note"]["en"].startswith("Guidance only")


def test_name_matching():
    assert precheck.names_match("Smt. Sunita Devi", "SUNITA DEVI")
    assert precheck.names_match("Sunita Devi", "Sunita")
    assert not precheck.names_match("Sunita Devi", "Kamla Prasad")


async def test_application_keeps_its_scheme_version(env, tokens):
    from app.services.schemes import load_seed_files

    app = await _draft(env)
    body = next(r for r in load_seed_files()[0] if r["slug"] == "ignoaps")
    body = {**body, "rules": {"all": [{"id": "age", "field": "age", "op": "gte", "value": 80}]},
            "change_note": "test: raise age"}
    r = await env["client"].put("/api/admin/schemes/ignoaps", headers=bearer(tokens["admin"]), json=body)
    assert r.json()["version"] == 2
    pre = (await env["client"].post(f"/api/applications/{app['id']}/precheck", headers=bearer(env["tok"]))).json()
    assert pre["scheme_version"] == 1 and pre["eligibility"]["status"] == "eligible"  # judged under v1


async def test_assignment_prefers_region_and_load(env, tokens):
    c = env["client"]
    for login_id, region in (("bihar.officer@test.in", "Bihar"), ("up.officer@test.in", "Uttar Pradesh")):
        r = await c.post("/api/admin/officers", headers=bearer(tokens["admin"]), json={
            "login_id": login_id, "full_name": "O " + region, "password": "Str0ng!Officer", "region": region})
        assert r.status_code == 201
    chosen = await apps.pick_officer(env["db"], "ignoaps", "Bihar")
    assert (await env["db"].officers.find_one({"user_id": chosen}))["region"] == "Bihar"
    # load balancing: give the chosen officer an open case, the other Bihar officer gets the next one
    await env["db"].applications.insert_one({"_id": "x", "assigned_officer_id": chosen, "status": "under_review"})
    nxt = await apps.pick_officer(env["db"], "ignoaps", "Bihar")
    assert nxt != chosen and (await env["db"].officers.find_one({"user_id": nxt}))["region"] == "Bihar"


async def test_use_once_cannot_apply(env):
    await env["db"].users.update_one({"_id": env["user_id"]}, {"$set": {"consent.mode": "use_once"}})
    r = await env["client"].post("/api/applications", headers=bearer(env["tok"]), json={"scheme": "ignoaps"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "consent_mode_use_once"


async def test_unknown_scheme(env):
    r = await env["client"].post("/api/applications", headers=bearer(env["tok"]), json={"scheme": "nope"})
    assert r.status_code == 404


_ = Path
