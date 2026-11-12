"""Rules engine: pure, deterministic, three-valued."""

from __future__ import annotations

import pytest

from app.services import eligibility as E
from app.services.schemes import load_seed_files

APY = {"all": [
    {"id": "age", "field": "age", "op": "between", "value": [18, 40]},
    {"id": "bank", "field": "has_bank_account", "op": "is_true"},
    {"id": "tax", "field": "is_income_tax_payer", "op": "is_false"},
]}


def test_all_pass_is_eligible():
    ev = E.evaluate(APY, {"age": 30, "has_bank_account": True, "is_income_tax_payer": False})
    assert ev.status == "eligible" and ev.passed == ["age", "bank", "tax"] and ev.score == 1.0


def test_one_fail_is_not_eligible():
    ev = E.evaluate(APY, {"age": 45, "has_bank_account": True, "is_income_tax_payer": False})
    assert ev.status == "not_eligible" and ev.failed == ["age"]


def test_missing_field_is_maybe():
    ev = E.evaluate(APY, {"age": 30, "has_bank_account": True})
    assert ev.status == "maybe" and ev.unknown == ["tax"]


def test_fail_beats_unknown():
    assert E.evaluate(APY, {"age": 70}).status == "not_eligible"


@pytest.mark.parametrize("age,expected", [(17, "not_eligible"), (18, "eligible"), (40, "eligible"),
                                          (41, "not_eligible")])
def test_between_is_inclusive(age, expected):
    assert E.evaluate(APY, {"age": age, "has_bank_account": True, "is_income_tax_payer": False}).status == expected


ANY = {"all": [{"any": [
    {"id": "bpl", "field": "is_bpl", "op": "is_true"},
    {"id": "inc", "field": "annual_income", "op": "lte", "value": 100000},
]}]}


@pytest.mark.parametrize("profile,expected", [
    ({"is_bpl": True}, "eligible"),               # one pass is enough
    ({"is_bpl": False, "annual_income": 50000}, "eligible"),
    ({"is_bpl": False, "annual_income": 500000}, "not_eligible"),
    ({"is_bpl": False}, "maybe"),                 # fail + unknown -> unknown
    ({}, "maybe"),
])
def test_any_group(profile, expected):
    assert E.evaluate(ANY, profile).status == expected


def test_not_group():
    rules = {"all": [{"not": {"id": "gov", "field": "is_govt_employee", "op": "is_true"}}]}
    assert E.evaluate(rules, {"is_govt_employee": True}).status == "not_eligible"
    assert E.evaluate(rules, {"is_govt_employee": False}).status == "eligible"
    assert E.evaluate(rules, {}).status == "maybe"


@pytest.mark.parametrize("op,value,actual,ok", [
    ("eq", "female", "female", True), ("eq", "female", "male", False),
    ("ne", "male", "female", True), ("in", ["sc", "st"], "st", True), ("in", ["sc", "st"], "obc", False),
    ("not_in", ["general"], "obc", True), ("lt", 10, 9, True), ("lt", 10, 10, False),
    ("lte", 10, 10, True), ("gt", 10, 11, True), ("gte", 10, 10, True),
])
def test_operators(op, value, actual, ok):
    field = {"eq": "gender", "ne": "gender", "in": "category", "not_in": "category"}.get(op, "family_size")
    rules = {"all": [{"id": "r", "field": field, "op": op, "value": value}]}
    assert (E.evaluate(rules, {field: actual}).status == "eligible") is ok


def test_invalid_profile_values_are_ignored_not_crashing():
    ev = E.evaluate(APY, {"age": "thirty", "has_bank_account": "yes", "unknown_field": 1, "is_income_tax_payer": None})
    assert ev.status == "maybe" and set(ev.unknown) == {"age", "bank", "tax"}


def test_deterministic():
    p = {"age": 30, "has_bank_account": True}
    assert E.evaluate(APY, p).to_dict() == E.evaluate(APY, p).to_dict()


def test_result_tree_has_bilingual_text_and_evidence():
    leaf = E.evaluate(APY, {"age": 25})._asdict() if False else E.evaluate(APY, {"age": 25}).to_dict()["tree"]["children"][0]
    assert leaf["text"]["en"] == "Age between 18 years and 40 years"
    assert leaf["text"]["hi"] == "आयु 18 वर्ष से 40 वर्ष के बीच"
    assert leaf["evidence"] == {"en": "You: 25 years", "hi": "आप: 25 वर्ष"}


def test_income_formatting_indian_grouping():
    assert E.format_value("annual_income", 250000) == "₹2,50,000"
    assert E.format_value("annual_income", 12000000) == "₹1,20,00,000"


def test_ranking_prefers_eligible_then_score_then_benefit():
    a = E.evaluate(APY, {"age": 30, "has_bank_account": True, "is_income_tax_payer": False})
    b = E.evaluate(APY, {"age": 30, "has_bank_account": True})
    c = E.evaluate(APY, {"age": 60})
    keys = sorted([("c", E.rank_key(c, 10**6)), ("b", E.rank_key(b, 10)), ("a", E.rank_key(a, 1))],
                  key=lambda x: x[1], reverse=True)
    assert [k for k, _ in keys] == ["a", "b", "c"]

# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize("rules,msg", [
    ({"any": []}, "top level"),
    ({"all": []}, "non-empty"),
    ({"all": [{"id": "x", "field": "salary", "op": "eq", "value": 1}]}, "unknown field"),
    ({"all": [{"id": "x", "field": "age", "op": "approx", "value": 1}]}, "unknown op"),
    ({"all": [{"id": "x", "field": "age", "op": "between", "value": [40, 18]}]}, "min > max"),
    ({"all": [{"id": "x", "field": "is_bpl", "op": "eq", "value": True}]}, "is_true"),
    ({"all": [{"id": "x", "field": "gender", "op": "eq", "value": "other"}]}, "not one of"),
    ({"all": [{"id": "x", "field": "age", "op": "gte", "value": "18"}]}, "number"),
    ({"all": [{"id": "x", "field": "age", "op": "gte", "value": 1},
              {"id": "x", "field": "age", "op": "lte", "value": 9}]}, "duplicate"),
    ({"all": [{"id": "x", "field": "state", "op": "eq", "value": "Atlantis"}]}, "unknown state"),
])
def test_validator_rejects_bad_rules(rules, msg):
    with pytest.raises(E.RuleError, match=msg):
        E.validate_rules(rules)


def test_all_seed_schemes_have_valid_rules():
    rows, catalog = load_seed_files()
    assert len(rows) == 30
    assert len({r["slug"] for r in rows}) == 30
    for r in rows:
        E.validate_rules(r["rules"])
        assert all(d in catalog for d in r["documents"]), r["slug"]


def _seed(slug):
    return next(r for r in load_seed_files()[0] if r["slug"] == slug)


def test_seed_sanity_old_age_pension():
    rules = _seed("ignoaps")["rules"]
    assert E.evaluate(rules, {"age": 65, "is_bpl": True}).status == "eligible"
    assert E.evaluate(rules, {"age": 45, "is_bpl": True}).status == "not_eligible"


def test_seed_sanity_atal_pension():
    rules = _seed("atal-pension-yojana")["rules"]
    assert E.evaluate(rules, {"age": 30, "has_bank_account": True, "is_income_tax_payer": False}).status == "eligible"
    assert E.evaluate(rules, {"age": 30, "has_bank_account": True, "is_income_tax_payer": True}).status == "not_eligible"
