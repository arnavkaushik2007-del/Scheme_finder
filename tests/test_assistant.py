"""Step 4: retrieval, assistant with citations + hallucination guard, recommendations, what's missing."""

from __future__ import annotations

import pytest

from app.ai import assistant, guard, index, llm
from app.seed import seed
from app.services import eligibility, recommend
from tests.conftest import USER, bearer, login


@pytest.fixture
async def seeded(db):
    await seed(db)
    await index.rebuild(db)
    return db


@pytest.fixture
async def user_tok(client, seeded, accounts):
    await seeded.users.update_one({"login_id": USER["login_id"]},
                                  {"$set": {"consent": {"mode": "save_profile", "accepted_at": "x"}}})
    return await login(client, USER, "user")


class FakeLLM:
    """Scripted generator + entailment judge."""

    name = "fake"

    def __init__(self, answer: str, entail: list[bool] | None = None):
        self.answer, self.entail, self.calls = answer, entail, []

    async def complete(self, system, prompt, *, max_tokens=2000):
        self.calls.append(prompt)
        return llm.LLMResult(text=self.answer, model="fake", is_mock=False)

    async def complete_json(self, system, prompt, schema, *, max_tokens=2000):
        n = prompt.count("CLAIM:")
        flags = self.entail if self.entail is not None else [True] * n
        return llm.LLMResult(data={"supported": flags[:n]}, model="fake", is_mock=False)

# ----------------------------------------------------------------------------- guard units


def test_split_keeps_citations_and_list_numbers():
    parts = guard.split_sentences("Steps: 1. Visit the office. [S1] It costs nothing. [S2][S3]")
    assert parts == ["Steps: 1. Visit the office. [S1]", "It costs nothing. [S2][S3]"]


async def test_guard_drops_uncited_and_wrong_numbers():
    sources = {1: "Atal Pension Yojana pays a pension of 1,000 to 5,000 rupees per month after age 60."}
    res = await guard.check(
        "The pension is 1,000 to 5,000 rupees per month after age 60. [S1] "
        "It pays 9,000 rupees per month. [S1] "
        "Everyone is approved automatically.", sources)
    oks = [s.ok for s in res.sentences]
    assert oks == [True, False, False]
    assert not res.sentences[1].numbers_ok and not res.sentences[2].has_citation
    assert res.method == "lexical"


async def test_guard_rejects_citation_to_unknown_source():
    res = await guard.check("Free cooking gas for women. [S9]", {1: "Free cooking gas for women."})
    assert res.sentences[0].ok is False


def test_language_detection():
    assert assistant.detect_language("अटल पेंशन योजना क्या है?") == "hi"
    assert assistant.detect_language("What is Atal Pension Yojana?") == "en"

# ----------------------------------------------------------------------------- assistant


async def test_extractive_answer_is_cited_and_grounded(client, user_tok):
    r = await client.post("/api/ask", headers=bearer(user_tok),
                          json={"question": "Which documents are needed for PM Kisan Samman Nidhi?"})
    d = r.json()
    assert r.status_code == 200 and d["abstained"] is False, d
    assert d["mode"] == "extractive" and "[S" in d["answer"]
    cited = [s for s in d["sources"] if s["cited"]]
    assert cited and cited[0]["slug"] == "pm-kisan" and cited[0]["section"] == "documents"
    assert all(s["ok"] for s in d["guard"]["sentences"])
    assert d["confidence"] >= 0.6


async def test_off_topic_question_abstains_with_portal_link(client, user_tok):
    d = (await client.post("/api/ask", headers=bearer(user_tok),
                           json={"question": "zzqx flurb wobble quantum giraffe?"})).json()
    assert d["abstained"] is True and d["abstain_reason"] == "low_retrieval"
    assert "https://www.myscheme.gov.in" in d["answer"] and d["confidence"] == 0.0


async def test_hindi_question_gets_hindi_sources(client, user_tok):
    d = (await client.post("/api/ask", headers=bearer(user_tok),
                           json={"question": "अटल पेंशन योजना के लिए आयु सीमा क्या है?"})).json()
    assert d["language"] == "hi"
    if not d["abstained"]:
        assert all(s["lang"] == "hi" for s in d["sources"] if s["cited"])


async def test_scope_to_scheme(client, user_tok):
    d = (await client.post("/api/ask", headers=bearer(user_tok),
                           json={"question": "How do I apply?", "scheme": "pm-ujjwala-yojana"})).json()
    assert {s["slug"] for s in d["sources"]} == {"pm-ujjwala-yojana"}
    assert (await client.post("/api/ask", headers=bearer(user_tok),
                              json={"question": "How do I apply?", "scheme": "nope"})).status_code == 404


async def test_eligibility_question_uses_rules_engine_not_model(client, user_tok):
    await client.put("/api/me/profile", headers=bearer(user_tok),
                     json={"eligibility": {"age": 66, "is_bpl": True}})
    d = (await client.post("/api/ask", headers=bearer(user_tok),
                           json={"question": "Am I eligible?", "scheme": "ignoaps"})).json()
    first = d["sources"][0]
    assert first["section"] == "rules_engine" and first["cited"]
    assert "likely eligible" in first["text"] and "likely eligible" in d["answer"]


async def test_generative_hallucination_is_removed(client, user_tok):
    fake = FakeLLM("Atal Pension Yojana is for people aged 18 to 40. [S1] "
                   "It guarantees a pension of 50,000 rupees a month. [S1]")
    llm.set_llm(fake)
    d = (await client.post("/api/ask", headers=bearer(user_tok),
                           json={"question": "Who can join Atal Pension Yojana?", "scheme": "atal-pension-yojana"})).json()
    assert d["mode"] == "generative" and not d["abstained"], d
    assert "50,000" not in d["answer"] and "18" in d["answer"]
    bad = [s for s in d["guard"]["sentences"] if not s["ok"]]
    assert len(bad) == 1 and bad[0]["numbers_ok"] is False
    assert d["guard"]["method"] == "llm-entailment+numbers"
    assert "<sources>" in fake.calls[0]


async def test_generative_not_found_and_judge_rejection_abstain(client, user_tok):
    llm.set_llm(FakeLLM("NOT_FOUND"))
    d = (await client.post("/api/ask", headers=bearer(user_tok), json={"question": "pension for old people"})).json()
    assert d["abstained"] and d["abstain_reason"] == "model_not_found"
    llm.set_llm(FakeLLM("Old age pension is for people aged 60 and above. [S1]", entail=[False]))
    d = (await client.post("/api/ask", headers=bearer(user_tok),
                           json={"question": "old age pension age", "scheme": "ignoaps"})).json()
    assert d["abstained"] and d["abstain_reason"] == "guard_failed"


async def test_chat_log_masks_ids_feedback_and_admin_queue(client, seeded, user_tok, tokens):
    d = (await client.post("/api/ask", headers=bearer(user_tok),
                           json={"question": "my aadhaar 2345 6789 0123 xyzzy flurb?"})).json()
    row = await seeded.chat_logs.find_one({"_id": d["id"]})
    assert "2345 6789" not in row["question"] and "XXXX XXXX 0123" in row["question"]
    assert (await client.post(f"/api/ask/{d['id']}/feedback", headers=bearer(user_tok),
                              json={"rating": "down", "comment": "wrong"})).status_code == 200
    # someone else cannot rate this answer
    assert (await client.post(f"/api/ask/{d['id']}/feedback", headers=bearer(tokens["officer"]),
                              json={"rating": "up"})).status_code == 404
    q = await client.get("/api/admin/ai/queue", headers=bearer(tokens["admin"]))
    assert q.status_code == 200 and q.json()[0]["id" if "id" in q.json()[0] else "_id"] == d["id"]
    assert "user_id" not in q.json()[0]
    assert (await client.get("/api/admin/ai/queue", headers=bearer(user_tok))).status_code == 403


async def test_ask_requires_login(client, seeded):
    assert (await client.post("/api/ask", json={"question": "hello there"})).status_code == 401


async def test_hybrid_search(client, seeded):
    r = (await client.get("/api/search", params={"q": "pension"})).json()
    top = [x["slug"] for x in r["results"][:6]]
    assert "atal-pension-yojana" in top and "ignoaps" in top

# ----------------------------------------------------------------------------- recommendations


def _tree(rules, profile):
    return eligibility.evaluate(rules, profile).to_dict()["tree"]


def test_whats_missing_income_gap_and_age_wait():
    rules = {"all": [{"id": "inc", "field": "annual_income", "op": "lte", "value": 100000},
                     {"id": "age", "field": "age", "op": "gte", "value": 60},
                     {"id": "bpl", "field": "is_bpl", "op": "is_true"}]}
    m = recommend.whats_missing(_tree(rules, {"annual_income": 120000, "age": 57}))
    by = {x["rule_id"]: x for x in m}
    assert by["inc"]["en"] == "Income is ₹20,000 above the limit of ₹1,00,000."
    assert by["age"]["kind"] == "wait" and "in 3 years" in by["age"]["en"]
    assert by["bpl"]["kind"] == "unknown"


def test_whats_missing_skips_passing_any_group():
    rules = {"all": [{"any": [{"id": "a", "field": "is_bpl", "op": "is_true"},
                              {"id": "b", "field": "annual_income", "op": "lte", "value": 1000}]}]}
    assert recommend.whats_missing(_tree(rules, {"is_bpl": True, "annual_income": 5000})) == []
    m = recommend.whats_missing(_tree(rules, {"is_bpl": False, "annual_income": 5000}))
    assert {x["rule_id"] for x in m} == {"a", "b"} and all(x["option_of_group"] for x in m)


async def test_recommendations_endpoint(client, user_tok):
    await client.put("/api/me/profile", headers=bearer(user_tok), json={"eligibility": {
        "age": 66, "gender": "female", "is_bpl": True, "marital_status": "widowed", "state": "Bihar",
        "residence": "rural", "annual_income": 50000, "has_bank_account": True}})
    r = await client.get("/api/recommendations", headers=bearer(user_tok))
    d = r.json()
    statuses = [i["result"]["status"] for i in d["items"]]
    assert statuses == sorted(statuses, key=lambda s: recommend.WEIGHT[s], reverse=True)
    slugs = [i["scheme"]["slug"] for i in d["items"]]
    assert "ladli-behna-mp" not in slugs  # other state's scheme
    assert "elderly" in d["groups"] and "widows" in d["groups"]
    chosen = set(d["benefit_estimate"]["schemes"])
    assert not ({"ignoaps", "ignwps"} <= chosen)  # mutually exclusive pensions are not both counted
    maybe = next(i for i in d["items"] if i["result"]["status"] == "maybe")
    assert maybe["whats_missing"]


async def test_recommendations_role_and_use_once(client, user_tok, tokens):
    assert (await client.get("/api/recommendations", headers=bearer(tokens["officer"]))).status_code == 403
    r = await client.post("/api/recommendations", headers=bearer(user_tok),
                          json={"profile": {"age": 30, "occupation": "street_vendor"}})
    top = r.json()["items"][0]
    assert top["result"]["status"] in ("eligible", "maybe")
