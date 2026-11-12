"""Ranked recommendations with reasons and "what's missing".

Eligibility comes only from the rules engine. Ranking inside each status group is content-based:
how many rules are met, how relevant the scheme's target groups are to this person, and the size
of the benefit. "What's missing" turns each failed or unknown rule into a plain next step, with
the numeric gap where there is one ("income is ₹20,000 above the limit", "eligible in 3 years").
"""

from __future__ import annotations

import math
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services import eligibility, schemes

WEIGHT = {"eligible": 2, "maybe": 1, "not_eligible": 0}


def person_groups(p: dict[str, Any]) -> set[str]:
    g: set[str] = set()
    if p.get("gender") == "female":
        g |= {"women", "families"}
    if (p.get("age") or 0) >= 60:
        g.add("elderly")
    if 15 <= (p.get("age") or 0) <= 35:
        g.add("youth")
    if p.get("occupation") == "student":
        g.add("students")
    if p.get("occupation") in ("farmer", "agricultural_labourer"):
        g.add("farmers")
    if p.get("occupation") in ("unorganised_worker", "agricultural_labourer", "street_vendor", "artisan", "fisher"):
        g.add("workers")
    if p.get("occupation") in ("self_employed", "street_vendor", "artisan"):
        g.add("entrepreneurs")
    if (p.get("disability_percent") or 0) >= 40:
        g.add("disabled")
    if p.get("marital_status") == "widowed":
        g.add("widows")
    if p.get("category") in ("sc", "st"):
        g.add("sc_st")
    if p.get("is_minority"):
        g.add("minorities")
    if p.get("has_girl_child_under_10"):
        g.add("girls")
    return g


def relevance(scheme: dict[str, Any], groups: set[str]) -> float:
    target = set(scheme.get("beneficiary_groups", []))
    if not target:
        return 0.3
    return len(target & groups) / len(target)


def _gap(leaf: dict[str, Any], actual: Any) -> dict[str, str] | None:
    f, op, v = leaf["field"], leaf["op"], leaf.get("value")
    fmt = eligibility.format_value
    label = eligibility.FIELDS[f]
    if actual is None:
        return {"kind": "unknown",
                "en": f"Tell us your {label.en.lower()} — it decides this rule.",
                "hi": f"अपना {label.hi} बताएँ — इसी से यह नियम तय होगा।"}
    if f in ("annual_income", "monthly_income") and op in ("lte", "lt"):
        diff = actual - v
        return {"kind": "hard", "en": f"Income is {fmt(f, diff, 'en')} above the limit of {fmt(f, v, 'en')}.",
                "hi": f"आय सीमा {fmt(f, v, 'hi')} से {fmt(f, diff, 'hi')} अधिक है।"}
    if f == "age":
        lo = v[0] if op == "between" else v if op in ("gte", "gt") else None
        hi_ = v[1] if op == "between" else v if op in ("lte", "lt") else None
        if lo is not None and actual < lo:
            yrs = lo - actual + (1 if op == "gt" else 0)
            return {"kind": "wait", "en": f"Eligible after turning {lo + (1 if op == 'gt' else 0)} (in {yrs} year{'s' if yrs != 1 else ''}).",
                    "hi": f"{lo + (1 if op == 'gt' else 0)} वर्ष की आयु होने पर पात्र ({yrs} वर्ष में)।"}
        if hi_ is not None and actual > hi_:
            return {"kind": "hard", "en": f"Age is above the limit of {hi_}.", "hi": f"आयु सीमा {hi_} से अधिक है।"}
    if op in ("gte", "gt") and isinstance(actual, (int, float)):
        return {"kind": "hard", "en": f"Needs {eligibility.describe_rule(leaf, 'en').lower()} (you: {fmt(f, actual, 'en')}).",
                "hi": f"ज़रूरी: {eligibility.describe_rule(leaf, 'hi')} (आप: {fmt(f, actual, 'hi')})।"}
    return {"kind": "hard", "en": f"Requires — {eligibility.describe_rule(leaf, 'en')} (you: {fmt(f, actual, 'en')}).",
            "hi": f"ज़रूरी — {eligibility.describe_rule(leaf, 'hi')} (आप: {fmt(f, actual, 'hi')})।"}


def whats_missing(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Failed/unknown leaves that block the result. Inside an "any" group that already passes,
    nothing is missing; inside a failing "any", one option is enough, so they are grouped."""
    out: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], in_any: bool = False) -> None:
        if node["kind"] == "leaf":
            if node["outcome"] != "pass":
                leaf = {"id": node["id"], "field": node["field"], "op": node["op"], "value": node["value"]}
                gap = _gap(leaf, node["actual"])
                if gap:
                    out.append({"rule_id": node["id"], "option_of_group": in_any, **gap})
            return
        if node["kind"] == "any":
            if node["outcome"] == "pass":
                return
            for c in node["children"]:
                walk(c, True)
            return
        if node["kind"] == "not":
            if node["outcome"] != "pass":
                out.append({"rule_id": "not", "option_of_group": in_any, "kind": "hard",
                            "en": "An exclusion condition applies.", "hi": "एक अपवर्जन शर्त लागू होती है।"})
            return
        for c in node["children"]:
            walk(c, in_any)

    walk(tree)
    return out


async def recommend(db: AsyncIOMotorDatabase, profile: dict[str, Any], *, include_not_eligible: bool = True,
                    category: str | None = None, level: str | None = None,
                    benefit_type: str | None = None) -> dict[str, Any]:
    clean = eligibility.clean_profile(profile)
    groups = person_groups(clean)
    rows = await schemes.list_schemes(db, category=category, level=level, benefit_type=benefit_type)
    max_benefit = max((r["annual_benefit_inr"] for r in rows), default=1) or 1
    items = []
    for s in rows:
        if s["level"] == "state" and clean.get("state") not in (None, s["state"]):
            continue
        ev = eligibility.evaluate(s["rules"], clean)
        if ev.status == "not_eligible" and not include_not_eligible:
            continue
        rel = relevance(s, groups)
        benefit_norm = math.log1p(s["annual_benefit_inr"]) / math.log1p(max_benefit)
        score = 0.5 * ev.score + 0.3 * rel + 0.2 * benefit_norm
        result = ev.to_dict()
        items.append({
            "scheme": {k: s[k] for k in ("id", "slug", "name", "summary", "benefit", "category", "level", "state",
                                         "benefit_type", "annual_benefit_inr", "official_url", "last_verified",
                                         "version", "deadline", "exclusive_with", "beneficiary_groups")},
            "result": result, "relevance": round(rel, 3), "rank_score": round(score, 4),
            "whats_missing": whats_missing(result["tree"]) if ev.status != "eligible" else [],
        })
    items.sort(key=lambda i: (WEIGHT[i["result"]["status"]], i["rank_score"]), reverse=True)

    # Benefit estimate: greedily take eligible schemes by benefit, skipping mutually exclusive ones.
    chosen: list[str] = []
    blocked: set[str] = set()
    for i in sorted((i for i in items if i["result"]["status"] == "eligible"),
                    key=lambda i: -i["scheme"]["annual_benefit_inr"]):
        slug = i["scheme"]["slug"]
        if slug in blocked:
            continue
        chosen.append(slug)
        blocked |= set(i["scheme"]["exclusive_with"])
    total = sum(i["scheme"]["annual_benefit_inr"] for i in items if i["scheme"]["slug"] in chosen)
    counts = {k: sum(1 for i in items if i["result"]["status"] == k) for k in WEIGHT}
    return {"counts": counts, "items": items, "groups": sorted(groups), "profile_used": clean,
            "benefit_estimate": {"total_annual_inr": total, "schemes": chosen,
                                 "note": "Rough estimate; insurance cover and loans are counted at face value."},
            "ranking": "rules engine status, then 0.5×rules met + 0.3×target-group relevance + 0.2×benefit size"}
