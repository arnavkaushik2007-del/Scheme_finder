"""Step 8: adaptive interview, what-if, planner, scam checker, change tracker, voice flag, evaluation."""

from __future__ import annotations

import pytest

from app.ai import index
from app.seed import seed
from app.services import interview, planner, scam, whatif
from app.services.schemes import load_seed_files
from tests.conftest import USER, bearer, login


@pytest.fixture
async def env(client, db, accounts):
    await seed(db)
    await index.rebuild(db)
    await db.users.update_one({"login_id": USER["login_id"]},
                              {"$set": {"consent": {"mode": "save_profile", "accepted_at": "x"}}})
    return {"tok": await login(client, USER, "user")}


def _rules(slug):
    return next(r for r in load_seed_files()[0] if r["slug"] == slug)["rules"]

# ----------------------------------------------------------------------------- interview


async def test_interview_narrows_and_finishes(client, env):
    h = bearer(env["tok"])
    answers: dict = {}
    truth = {"age": 67, "gender": "female", "state": "Bihar", "is_bpl": True, "marital_status": "widowed",
             "annual_income": 60000, "occupation": "homemaker", "has_bank_account": True, "residence": "rural"}
    seen = []
    for _ in range(25):
        d = (await client.post("/api/interview/next", headers=h, json={"answers": answers, "skipped": []})).json()
        seen.append(d["possible"])
        if d["done"]:
            break
        f = d["question"]["field"]
        assert d["question"]["text"]["hi"] and f not in answers
        if f in truth:
            answers[f] = truth[f]
        else:
            r = await client.post("/api/interview/next", headers=h, json={"answers": answers, "skipped": [f]})
            assert r.json().get("question") is None or r.json()["question"]["field"] != f
            answers[f] = {"bool": False}.get(d["question"]["kind"], d["question"]["min"] or 0) \
                if d["question"]["kind"] in ("bool", "int", "number") else next(iter(d["question"]["options"]))
    assert d["done"] and seen == sorted(seen, reverse=True)  # never grows
    assert d["asked"] < 24 and d["eligible"] >= 1


def test_first_question_is_most_informative():
    rows = [{**r, "id": r["slug"]} for r in load_seed_files()[0]]
    field, expected = interview.choose_next(rows, {}, set())
    assert field is not None and expected < len(rows)
    for other in ("has_lpg_connection", "has_girl_child_under_10"):  # rarely-used fields are worse first questions
        vals = interview._candidates(other, [r["rules"] for r in rows])
        exp_other = sum(len(interview._possible(rows, {other: v})) for v in vals) / len(vals)
        assert expected <= exp_other


async def test_interview_requires_citizen(client, env, tokens):
    assert (await client.post("/api/interview/next", headers=bearer(tokens["officer"]), json={})).status_code == 403

# ----------------------------------------------------------------------------- what-if


def test_whatif_wait_for_age():
    w = whatif.whatif(_rules("ignoaps"), {"age": 57, "is_bpl": True})
    assert w["feasible"] and w["after"] == "eligible"
    assert w["changes"][0]["kind"] == "wait" and "3" in w["changes"][0]["text"]["en"]


def test_whatif_cannot_get_younger():
    w = whatif.whatif(_rules("atal-pension-yojana"), {"age": 45, "has_bank_account": True, "is_income_tax_payer": False})
    assert not w["feasible"] and w["changes"][0]["kind"] == "fixed"


def test_whatif_easy_action_and_already_eligible():
    w = whatif.whatif(_rules("atal-pension-yojana"), {"age": 30, "has_bank_account": False, "is_income_tax_payer": False})
    assert w["feasible"] and w["changes"] == [w["changes"][0]] and w["changes"][0]["kind"] == "action"
    assert whatif.whatif(_rules("ignoaps"), {"age": 70, "is_bpl": True})["already_eligible"]


async def test_whatif_api(client, env):
    await client.put("/api/me/profile", headers=bearer(env["tok"]), json={"eligibility": {"age": 57, "is_bpl": True}})
    r = (await client.post("/api/whatif", headers=bearer(env["tok"]), json={"scheme": "ignoaps"})).json()
    assert r["feasible"]
    close = (await client.post("/api/whatif", headers=bearer(env["tok"]), json={})).json()["closest"]
    assert any(c["scheme"] == "ignoaps" for c in close)

# ----------------------------------------------------------------------------- planner


def test_best_combination_respects_exclusions():
    items = [{"slug": "a", "name": {"en": "A", "hi": "A"}, "annual_benefit_inr": 100, "exclusive_with": ["b"]},
             {"slug": "b", "name": {"en": "B", "hi": "B"}, "annual_benefit_inr": 300, "exclusive_with": ["a", "c"]},
             {"slug": "c", "name": {"en": "C", "hi": "C"}, "annual_benefit_inr": 250, "exclusive_with": ["b"]},
             {"slug": "d", "name": {"en": "D", "hi": "D"}, "annual_benefit_inr": 50, "exclusive_with": []}]
    chosen, conflicts = planner.best_combination(items)
    assert set(chosen) == {"a", "c", "d"}  # 100 + 250 beats B's 300 alone
    assert {c["drop"] for c in conflicts} == {"b"}


async def test_plan_and_pdf(client, env):
    h = bearer(env["tok"])
    await client.put("/api/me/profile", headers=h, json={"eligibility": {
        "age": 67, "gender": "female", "state": "Bihar", "is_bpl": True, "marital_status": "widowed",
        "annual_income": 60000, "has_bank_account": True, "residence": "rural"}})
    p = (await client.post("/api/plan", headers=h, json={})).json()
    slugs = [x["slug"] for x in p["timeline"]]
    assert not ({"ignoaps", "ignwps"} <= set(slugs)) and p["total_annual_inr"] > 0
    assert [x["order"] for x in p["timeline"]] == list(range(1, len(slugs) + 1))
    r = await client.get("/api/plan/pdf", headers=h)
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and r.headers["content-type"] == "application/pdf"

# ----------------------------------------------------------------------------- scam checker


def test_scam_classification():
    bad = scam.classify("Congratulations! Your PM Kisan money is blocked. Pay Rs 199 processing fee and share OTP "
                        "today only at bit.ly/pmk-help")
    assert bad["verdict"] == "likely_scam" and {f["code"] for f in bad["flags"]} >= {"fee_request", "otp_request",
                                                                                      "unofficial_link"}
    hi_bad = scam.classify("बधाई हो! आपको लॉटरी में 5 लाख मिले हैं, ₹500 जमा करें और अपना OTP भेजें")
    assert hi_bad["verdict"] == "likely_scam"
    ok = scam.classify("Your PM-KISAN instalment of Rs 2000 has been credited to your bank account. "
                       "For details visit pmkisan.gov.in. No fee is charged.")
    assert ok["verdict"] == "consistent"


async def test_scam_endpoint_public_and_not_stored(client, env, db):
    before = {c: await db[c].count_documents({}) for c in await db.list_collection_names()}
    r = await client.post("/api/scam-check", json={"text": "Free laptop for all students! Register at "
                                                           "free-laptop-yojana.in and pay Rs 99 fee"})
    d = r.json()
    assert r.status_code == 200 and d["verdict"] in ("likely_scam", "suspicious")
    assert d["report"]["helpline"] == "1930" and d["tips"]["hi"]
    after = {c: await db[c].count_documents({}) for c in await db.list_collection_names()}
    assert before == after

# ----------------------------------------------------------------------------- change tracker


async def test_updates_feed_and_affected_users(client, env, tokens, db):
    h = bearer(env["tok"])
    await client.put("/api/me/profile", headers=h, json={"eligibility": {"age": 60, "is_bpl": True}})
    body = next(r for r in load_seed_files()[0] if r["slug"] == "ignoaps")
    new_rules = {"all": [{"id": "age", "field": "age", "op": "gte", "value": 65},
                         {"id": "bpl", "field": "is_bpl", "op": "is_true"}]}
    r = await client.put("/api/admin/schemes/ignoaps", headers=bearer(tokens["admin"]),
                         json={**body, "rules": new_rules, "change_note": "age raised (test)"})
    assert r.status_code == 200
    feed = (await client.get("/api/updates")).json()
    item = next(u for u in feed if u["slug"] == "ignoaps")
    assert any("from 60 years to 65 years" in c["en"] for c in item["changes"])
    n = (await client.get("/api/notifications", headers=h)).json()
    hit = next(x for x in n["items"] if x["status"] == "rules_changed")
    assert hit["link"] == "/scheme/ignoaps" and "likely eligible → not eligible" in hit["note"]

# ----------------------------------------------------------------------------- voice + evaluation


async def test_voice_disabled_by_default(client, env):
    st = (await client.get("/api/voice/status")).json()
    assert st["enabled"] is False and st["tts"] == "browser speechSynthesis"
    r = await client.post("/api/stt", headers=bearer(env["tok"]), files={"file": ("a.webm", b"xxxx", "audio/webm")})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "voice_disabled"


async def test_evaluation_run(client, env, tokens, db):
    assert (await client.post("/api/admin/ai/evaluate", headers=bearer(env["tok"]))).status_code == 403
    r = await client.post("/api/admin/ai/evaluate", headers=bearer(tokens["admin"]))
    assert r.status_code == 200, r.text
    m = r.json()["metrics"]
    assert m["eligibility"]["n"] == 60 and m["eligibility"]["accuracy"] == 1.0
    assert m["rule_extraction"]["n"] == 10 and m["rule_extraction"]["f1"] is not None
    assert m["rag"]["n"] == 36 and m["rag"]["abstention"]["off_topic_declined"] is not None
    assert m["documents"]["field_accuracy"] >= 0.9
    assert m["scam"]["n"] == 30 and m["scam"]["precision"] is not None
    assert m["interview"]["avg_questions_adaptive"] < m["interview"]["questions_full_form"]
    assert m["speech"]["measured"] is False
    runs = (await client.get("/api/admin/ai/evaluations", headers=bearer(tokens["admin"]))).json()
    assert runs["runs"][0]["versions"]["prompts"]["assistant_answer"] and await db.eval_runs.count_documents({}) == 1


def test_no_duplicate_translation_keys():
    import re
    from collections import Counter
    from pathlib import Path

    src = Path("app/ui/i18n.py").read_text(encoding="utf-8")
    body = src[src.index("T: dict"):src.index("PW_PROBLEMS")]
    keys = re.findall(r'^\s+"([a-z0-9_]+)": \{', body, re.M)
    assert [k for k, c in Counter(keys).items() if c > 1] == []
