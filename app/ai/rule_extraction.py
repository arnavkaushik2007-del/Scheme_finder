"""Turn guideline text into a PROPOSED rule tree that waits in the admin's rules-review queue.

Two methods, both grounded in the text:
* pattern - deterministic regexes for the common clauses (age range, income limit, women, BPL,
            SC/ST, disability %, widows, income-tax payers, rural/urban, bank account, state).
* llm     - when a model is configured: it must return each rule with the exact sentence it came
            from; any rule whose quote is not found in the guideline is DROPPED (grounding check),
            and every rule is validated against the rules vocabulary.
Nothing goes live until an admin approves it.
"""

from __future__ import annotations

import re
from typing import Any

from app.ai.llm import get_llm
from app.services.eligibility import FIELDS, INDIAN_STATES, OPS, RuleError, validate_rules

_NUM = r"(\d[\d,]*(?:\.\d+)?)"


def _amount(num: str, unit: str | None) -> int:
    v = float(num.replace(",", ""))
    unit = (unit or "").lower()
    if unit.startswith(("lakh", "lac")):
        v *= 100_000
    elif unit.startswith("crore"):
        v *= 10_000_000
    elif unit in ("k", "thousand"):
        v *= 1000
    return int(round(v))


def _snippet(text: str, m: re.Match[str]) -> str:
    start = text.rfind(".", 0, m.start()) + 1
    end = text.find(".", m.end())
    return re.sub(r"\s+", " ", text[start:end if end != -1 else len(text)]).strip()[:300]


def pattern_rules(text: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    rules: list[dict[str, Any]] = []
    evidence: dict[str, str] = {}
    t = text

    def add(rid: str, field: str, op: str, value: Any, m: re.Match[str]) -> None:
        if any(r["id"] == rid for r in rules):
            return
        rule = {"id": rid, "field": field, "op": op}
        if value is not None:
            rule["value"] = value
        rules.append(rule)
        evidence[rid] = _snippet(t, m)

    if m := re.search(rf"(?i)(?:aged?|age group of)\s*(?:between|from)?\s*{_NUM}\s*(?:and|to|-|–)\s*{_NUM}\s*years", t):
        add("age", "age", "between", [int(float(m.group(1))), int(float(m.group(2)))], m)
    elif m := re.search(rf"(?i)(?:aged?|age of)\s*{_NUM}\s*years?\s*(?:or|and)\s*(?:above|more|older)", t) or \
            re.search(rf"(?i)(?:above|at least|minimum age(?: of)?)\s*(?:the age of\s*)?{_NUM}\s*years", t):
        add("age", "age", "gte", int(float(m.group(1))), m)
    elif m := re.search(rf"(?i)(?:below|under|less than)\s*(?:the age of\s*)?{_NUM}\s*years", t):
        add("age", "age", "lt", int(float(m.group(1))), m)

    if m := re.search(rf"(?i)(?:annual|yearly|family)\s+(?:family\s+)?income[^.]{{0,40}}?(?:not exceed(?:ing)?|up to|upto|below|less than|not more than|does not exceed|within)\s*(?:rs\.?|inr|₹)?\s*{_NUM}\s*(lakh|lac|crore|thousand|k)?", t):
        add("income", "annual_income", "lte", _amount(m.group(1), m.group(2)), m)
    elif m := re.search(rf"(?i)monthly income[^.]{{0,40}}?(?:not exceed(?:ing)?|up to|upto|below|less than)\s*(?:rs\.?|inr|₹)?\s*{_NUM}", t):
        add("monthly_income", "monthly_income", "lte", _amount(m.group(1), None), m)

    if m := re.search(r"(?i)\b(widows?|widowed)\b", t):
        add("widow", "marital_status", "eq", "widowed", m)
    if m := re.search(r"(?i)\b(women|woman|female|girls?)\b(?![^.]*\bmen\b)", t):
        add("gender", "gender", "eq", "female", m)
    if m := re.search(r"(?i)below poverty line|\bBPL\b", t):
        add("bpl", "is_bpl", "is_true", None, m)
    sc = re.search(r"(?i)scheduled castes?|\bSCs?\b(?=\s*(?:/|and|or|,|\)|candidates|applicants|category|communit))", t)
    st = re.search(r"(?i)scheduled tribes?|(?<=/|\s)\bSTs?\b(?=\s*(?:candidates|applicants|category|communit|\.|,|\)|$))", t)
    if sc and st:
        add("category", "category", "in", ["sc", "st"], sc)
    elif sc:
        add("category", "category", "eq", "sc", sc)
    elif st:
        add("category", "category", "eq", "st", st)
    if m := re.search(rf"(?i){_NUM}\s*%\s*(?:or more\s*)?(?:benchmark\s*)?disabilit", t) or \
            re.search(rf"(?i)disabilit[^.]{{0,30}}?(?:of|at least|minimum)\s*{_NUM}\s*%", t):
        add("disability", "disability_percent", "gte", int(float(m.group(1))), m)
    if m := re.search(r"(?i)(?:not|no|non)[- ]?(?:an?\s+)?income[- ]tax payers?|income[- ]tax payers? (?:are|is) not eligible", t):
        add("not_taxpayer", "is_income_tax_payer", "is_false", None, m)
    if m := re.search(r"(?i)\brural\b", t):
        if not re.search(r"(?i)\burban\b", t):
            add("rural", "residence", "eq", "rural", m)
    elif m := re.search(r"(?i)\burban\b", t):
        add("urban", "residence", "eq", "urban", m)
    if m := re.search(r"(?i)(?:savings |valid )?bank account", t):
        add("bank", "has_bank_account", "is_true", None, m)
    for state in sorted(INDIAN_STATES, key=len, reverse=True):
        if m := re.search(rf"(?i)(?:resident|domicile)s? of (?:the state of )?{re.escape(state)}", t):
            add("state", "state", "eq", state, m)
            break
    return rules, evidence


LLM_SYSTEM = """You extract eligibility rules from an Indian government scheme guideline.
Return only rules the text states explicitly. For each rule give the exact sentence from the text in "quote".
Fields and allowed values: {fields}. Operators: {ops}. Use is_true/is_false (no value) for yes/no fields.
Money is in rupees as a plain integer (1.5 lakh = 150000)."""


async def llm_rules(text: str) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    from app.services.eligibility import ENUMS

    fields = "; ".join(f"{k} ({v.kind}{': ' + '/'.join(ENUMS[k]) if k in ENUMS else ''})" for k, v in FIELDS.items())
    schema = {"type": "object", "properties": {"rules": {"type": "array", "items": {
        "type": "object", "properties": {"field": {"type": "string"}, "op": {"type": "string"},
                                         "value": {}, "quote": {"type": "string"}},
        "required": ["field", "op", "quote"], "additionalProperties": False}}},
        "required": ["rules"], "additionalProperties": False}
    res = await get_llm().complete_json(LLM_SYSTEM.format(fields=fields, ops=", ".join(OPS)),
                                        f"<guideline>\n{text[:20000]}\n</guideline>", schema, max_tokens=2500)
    rules, evidence, dropped = [], {}, []
    norm_text = re.sub(r"\s+", " ", text).lower()
    for i, r in enumerate(res.data.get("rules", []) if not res.error else []):
        quote = re.sub(r"\s+", " ", str(r.get("quote", ""))).strip()
        if not quote or quote.lower() not in norm_text:
            dropped.append(f"{r.get('field')}: quote not found in the guideline")
            continue
        rule = {"id": f"{r.get('field')}_{i}", "field": r.get("field"), "op": r.get("op")}
        if r.get("op") not in ("is_true", "is_false"):
            rule["value"] = r.get("value")
        try:
            validate_rules({"all": [rule]})
        except RuleError as exc:
            dropped.append(f"{r.get('field')}: {exc}")
            continue
        rules.append(rule)
        evidence[rule["id"]] = quote[:300]
    return rules, evidence, dropped


async def extract(text: str) -> dict[str, Any]:
    if get_llm().name != "mock":
        rules, evidence, dropped = await llm_rules(text)
        method = "llm"
        if not rules:  # fall back rather than propose nothing
            rules, evidence = pattern_rules(text)
            method = "pattern (llm found nothing grounded)"
    else:
        rules, evidence = pattern_rules(text)
        dropped, method = [], "pattern"
    tree = {"all": rules} if rules else None
    if tree:
        validate_rules(tree)
    return {"rules": tree, "evidence": evidence, "dropped": dropped, "method": method}
