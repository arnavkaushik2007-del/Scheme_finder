"""Step 6: officer queue, AI verification report, decision panel, history, stats, access control."""

from __future__ import annotations

import pytest

from app.seed import OFFICERS, seed
from app.services import verification
from scripts.make_sample_docs import DOCS, make_pdf
from tests.conftest import USER, bearer, login

FORM = {"eligibility": {"age": 68, "is_bpl": True, "state": "Bihar", "gender": "female"},
        "personal": {"full_name": "Sunita Devi", "date_of_birth": "1958-03-12", "aadhaar_last4": "8901"}}
PATNA = next(o for o in OFFICERS if o["region"] == "Bihar")
LUCKNOW = next(o for o in OFFICERS if o["region"] == "Uttar Pradesh")


@pytest.fixture(autouse=True)
def _files(settings, tmp_path):
    settings.file_storage_dir = str(tmp_path / "files")


async def _submit(client, tok, tmp_path, form=FORM, scheme="ignoaps"):
    ids = []
    for name, doc_type in (("aadhaar_sunita.pdf", "aadhaar"), ("ration_card_sunita.pdf", "ration_card")):
        title, lines = DOCS[name]
        make_pdf(tmp_path / name, title, lines)
        r = await client.post("/api/documents", headers=bearer(tok), data={"doc_type": doc_type},
                              files={"file": (name, (tmp_path / name).read_bytes(), "application/pdf")})
        ids.append(r.json()["id"])
    app = (await client.post("/api/applications", headers=bearer(tok),
                             json={"scheme": scheme, "form": form, "documents": ids})).json()
    r = await client.post(f"/api/applications/{app['id']}/submit", headers=bearer(tok), json={"declaration": True})
    assert r.status_code == 200, r.text
    return r.json(), ids


@pytest.fixture
async def env(client, db, accounts, tmp_path):
    await seed(db)
    await db.users.update_one({"login_id": USER["login_id"]},
                              {"$set": {"consent": {"mode": "save_profile", "accepted_at": "x"}}})
    tok = await login(client, USER, "user")
    app, docs = await _submit(client, tok, tmp_path)
    patna = await login(client, PATNA, "officer")
    lucknow = await login(client, LUCKNOW, "officer")
    return {"db": db, "c": client, "user": tok, "app": app, "docs": docs, "patna": patna, "lucknow": lucknow,
            "tmp": tmp_path}


async def test_queue_shows_assigned_case_with_ai_suggestion(env):
    q = (await env["c"].get("/api/officer/queue", headers=bearer(env["patna"]))).json()
    item = next(i for i in q["items"] if i["id"] == env["app"]["id"])
    assert item["status"] == "precheck_done" and item["ai_suggested"] in ("eligible", "needs_review")
    assert 0 < item["ai_confidence"] <= 1 and item["age_days"] >= 0
    assert q["counts"]["precheck_done"] == 1
    # the UP officer does not see it and cannot open it
    q2 = (await env["c"].get("/api/officer/queue", headers=bearer(env["lucknow"]))).json()
    assert all(i["id"] != env["app"]["id"] for i in q2["items"])
    for path in (f"/api/officer/applications/{env['app']['id']}",
                 f"/api/officer/applications/{env['app']['id']}/documents/{env['docs'][0]}/file"):
        assert (await env["c"].get(path, headers=bearer(env["lucknow"]))).status_code == 404
    r = await env["c"].post(f"/api/officer/applications/{env['app']['id']}/decision", headers=bearer(env["lucknow"]),
                            json={"action": "approve", "reason": "Trying to approve someone else's case"})
    assert r.status_code == 404


async def test_queue_filters(env):
    h = bearer(env["patna"])
    assert len((await env["c"].get("/api/officer/queue", headers=h, params={"scheme": "pm-kisan"})).json()["items"]) == 0
    assert len((await env["c"].get("/api/officer/queue", headers=h, params={"min_conf": 0.99})).json()["items"]) == 0
    assert len((await env["c"].get("/api/officer/queue", headers=h, params={"status": "precheck_done"})).json()["items"]) == 1


async def test_case_view_masks_aadhaar_and_shows_rule_evidence(env):
    d = (await env["c"].get(f"/api/officer/applications/{env['app']['id']}", headers=bearer(env["patna"]))).json()
    assert d["applicant"]["aadhaar_masked"] == "XXXX XXXX 8901"
    rules = {r["field"]: r for r in d["report"]["rules"]}
    assert rules["age"]["evidence"] == "supported" and rules["age"]["supporting"][0]["doc_type"] == "aadhaar"
    assert rules["is_bpl"]["evidence"] == "supported"  # AAY ration card
    assert d["report"]["note"]["en"].startswith("Advice only")
    assert {doc["doc_type"] for doc in d["documents"]} == {"aadhaar", "ration_card"}


async def test_document_view_is_audited(env, db):
    r = await env["c"].get(f"/api/officer/applications/{env['app']['id']}/documents/{env['docs'][0]}/file",
                           headers=bearer(env["patna"]))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert await db.audit_log.find_one({"action": "document.view", "target.id": env["docs"][0]})


async def test_full_decision_flow_with_ai_agreement_tracking(env, db):
    c, h, aid = env["c"], bearer(env["patna"]), env["app"]["id"]
    # cannot decide before starting review
    r = await c.post(f"/api/officer/applications/{aid}/decision", headers=h,
                     json={"action": "approve", "reason": "All rules are met here."})
    assert r.status_code == 409
    assert (await c.post(f"/api/officer/applications/{aid}/start-review", headers=h)).status_code == 200
    # a reason is mandatory
    r = await c.post(f"/api/officer/applications/{aid}/decision", headers=h, json={"action": "approve", "reason": "ok"})
    assert r.status_code == 422
    r = await c.post(f"/api/officer/applications/{aid}/decision", headers=h, json={
        "action": "request_docs", "reason": "Please upload your husband's death certificate.",
        "requested_documents": ["husband_death_certificate"]})
    d = r.json()
    assert d["application"]["status"] == "more_docs_needed"
    assert d["decision"]["ai_suggested"] and isinstance(d["decision"]["differs_from_ai"], bool)
    # citizen sees the officer's remark in the timeline and resubmits
    app = (await c.get(f"/api/applications/{aid}", headers=bearer(env["user"]))).json()
    assert "death certificate" in app["history"][-1]["note"]
    assert "ai_suggested" not in app and "latest_report_id" not in app  # officer-only fields hidden
    await c.post(f"/api/applications/{aid}/resubmit", headers=bearer(env["user"]),
                 json={"note": "Applied at the panchayat; attaching later"})
    await c.post(f"/api/officer/applications/{aid}/start-review", headers=h)
    r = await c.post(f"/api/officer/applications/{aid}/decision", headers=h,
                     json={"action": "approve", "reason": "Age and BPL status verified from documents."})
    assert r.json()["application"]["status"] == "approved"
    assert (await c.post(f"/api/officer/applications/{aid}/close", headers=h)).json()["status"] == "closed"
    hist = (await c.get("/api/officer/history", headers=h)).json()
    assert [x["action"] for x in hist] == ["approve", "request_docs"] and hist[0]["applicant"] == "Sunita Devi"
    stats = (await c.get("/api/officer/stats", headers=h)).json()
    assert stats["decisions"] == 2 and stats["cases_decided"] == 1 and stats["override_rate"] is not None
    actions = {e["action"] async for e in db.audit_log.find({"target.id": aid})}
    assert {"application.under_review", "application.more_docs_needed", "application.approved"} <= actions
    assert await db.decisions.count_documents({"application_id": aid}) == 2
    assert await db.ai_reports.count_documents({"application_id": aid}) >= 3  # submit, review, resubmit, review


async def test_override_is_recorded(env, db):
    c, h, aid = env["c"], bearer(env["patna"]), env["app"]["id"]
    await c.post(f"/api/officer/applications/{aid}/start-review", headers=h)
    ai = (await db.applications.find_one({"_id": aid}))["ai_suggested"]
    action = "reject" if ai != "not_eligible" else "approve"
    d = (await c.post(f"/api/officer/applications/{aid}/decision", headers=h,
                      json={"action": action, "reason": "Officer disagrees after a field visit."})).json()
    assert d["decision"]["differs_from_ai"] is True
    assert (await c.get("/api/officer/stats", headers=h)).json()["override_rate"] == 1.0


async def test_document_contradiction_flips_suggestion(env, db):
    """Form says age 68, but suppose the Aadhaar says born 1990: the age rule fails with the documented value."""
    app = await db.applications.find_one({"_id": env["app"]["id"]})
    aadhaar = next(d for d in [x async for x in db.documents.find({"_id": {"$in": app["documents"]}})]
                   if d["doc_type"] == "aadhaar")
    await db.documents.update_one({"_id": aadhaar["_id"]},
                                  {"$set": {"confirmed_fields": {"date_of_birth": "1990-01-01"}}})
    report = await verification.build(db, app)
    age = next(r for r in report["rules"] if r["field"] == "age")
    assert age["evidence"] == "contradicted" and age["outcome_with_document_value"] == "not_eligible"
    assert report["suggested"] == "not_eligible"


async def test_claim_unassigned(env, db):
    aid = env["app"]["id"]
    await db.applications.update_one({"_id": aid}, {"$set": {"assigned_officer_id": None}})
    q = (await env["c"].get("/api/officer/queue", headers=bearer(env["lucknow"]))).json()
    assert any(i["id"] == aid for i in q["unassigned"])
    r = await env["c"].post(f"/api/officer/applications/{aid}/claim", headers=bearer(env["lucknow"]))
    assert r.status_code == 200
    assert (await env["c"].post(f"/api/officer/applications/{aid}/claim", headers=bearer(env["patna"]))).status_code == 404


async def test_officer_endpoints_refuse_other_roles(env, tokens):
    for tok in (env["user"], tokens["admin"]):
        assert (await env["c"].get("/api/officer/queue", headers=bearer(tok))).status_code == 403
        assert (await env["c"].get("/api/officer/stats", headers=bearer(tok))).status_code == 403
