"""Counterfactuals (spec 3.6 / POST /whatif): the smallest change that turns a scheme into "likely eligible".

For each rule that fails (or is unknown) we compute the minimal value that would pass it. Inside
"all" groups every failing child must be fixed; inside "any" groups we take the cheapest option.
The combined change is re-checked with the rules engine, so a suggestion is only shown if it really
flips the result. Changes to things a person cannot change (gender, social category, state,
disability) are marked "not changeable" instead of being suggested as advice.
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services import eligibility, schemes
from app.services.errors import ServiceError

IMMUTABLE = {"gender", "category", "state", "is_minority", "disability_percent", "marital_status",
             "is_pregnant_or_lactating", "has_girl_child_under_10", "education_level", "marks_percent"}
EASY = {"has_bank_account": 1.0, "has_epf_esic_nps": 5.0, "has_lpg_connection": 5.0}


def _fix(leaf: dict[str, Any], actual: Any) -> tuple[Any, float] | None:
    """(new value, cost) that makes this single leaf pass; None if no single value does."""
    op, v, f = leaf["op"], leaf.get("value"), leaf["field"]
    if op == "is_true":
        new: Any = True
    elif op == "is_false":
        new = False
    elif op == "eq":
        new = v
    elif op == "in":
        new = v[0] if actual not in v else actual
    elif op in ("lte", "lt"):
        new = v if op == "lte" else v - (1 if eligibility.FIELDS[f].kind == "int" else 0.01)
    elif op in ("gte", "gt"):
        new = v if op == "gte" else v + (1 if eligibility.FIELDS[f].kind == "int" else 0.01)
    elif op == "between":
        new = v[0] if actual is None or actual < v[0] else v[1]
    else:  # ne / not_in: no specific target value
        return None
    if f in IMMUTABLE:
        cost = 1000.0
    elif f == "age":
        cost = 10.0 * abs(new - actual) if actual is not None and new > actual else 1000.0  # can only get older
    elif f in ("annual_income", "monthly_income"):
        cost = 20.0 + (abs(actual - new) / max(1.0, float(actual)) * 50 if actual is not None else 0)
    elif f in EASY:
        cost = EASY[f]
    else:
        cost = 30.0
    return new, cost


def _plan(node: dict[str, Any], profile: dict[str, Any]) -> tuple[dict[str, Any], float] | None:
    ev = eligibility.evaluate({"all": [node]} if "all" not in node else node, profile)
    if ev.status == "eligible":
        return {}, 0.0
    if "all" in node:
        changes: dict[str, Any] = {}
        total = 0.0
        for child in node["all"]:
            sub = _plan(child, {**profile, **changes})
            if sub is None:
                return None
            changes.update(sub[0])
            total += sub[1]
        return changes, total
    if "any" in node:
        options = [p for c in node["any"] if (p := _plan(c, profile)) is not None]
        return min(options, key=lambda p: p[1]) if options else None
    if "not" in node:
        return None
    fx = _fix(node, profile.get(node["field"]))
    return ({node["field"]: fx[0]}, fx[1]) if fx else None


def _describe(field: str, old: Any, new: Any, lang: str) -> str:
    fmt = eligibility.format_value
    label = eligibility.FIELDS[field].en if lang == "en" else eligibility.FIELDS[field].hi
    if field == "age" and old is not None and new > old:
        yrs = new - old
        return (f"Wait {yrs} year{'s' if yrs != 1 else ''} (until age {new})" if lang == "en"
                else f"{yrs} वर्ष रुकें ({new} वर्ष की आयु तक)")
    if field == "age" and old is not None and new < old:
        return (f"Age limit is {new} — you are {old} (cannot change)" if lang == "en"
                else f"आयु सीमा {new} है — आपकी आयु {old} (बदल नहीं सकती)")
    if old is None:
        return f"{label}: {fmt(field, new, lang)}" + (" (needs to be confirmed)" if lang == "en" else " (पुष्टि ज़रूरी)")
    return (f"{label}: {fmt(field, old, lang)} → {fmt(field, new, lang)}")


def whatif(rules: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    clean = eligibility.clean_profile(profile)
    before = eligibility.evaluate(rules, clean).status
    if before == "eligible":
        return {"status": before, "already_eligible": True, "changes": [], "feasible": True}
    plan = _plan(rules, clean)
    if plan is None:
        return {"status": before, "already_eligible": False, "changes": [], "feasible": False,
                "note": {"en": "No simple change makes this scheme apply.", "hi": "कोई आसान बदलाव इस योजना को लागू नहीं करता।"}}
    changes, cost = plan
    after = eligibility.evaluate(rules, {**clean, **changes}).status
    items = []
    for f, new in changes.items():
        old = clean.get(f)
        if old is None:
            kind = "unknown"
        elif f in IMMUTABLE or (f == "age" and new < old):  # nobody gets younger
            kind = "fixed"
        else:
            kind = "wait" if f == "age" else "action" if f in EASY else "change"
        items.append({"field": f, "from": old, "to": new, "kind": kind,
                      "text": {"en": _describe(f, old, new, "en"), "hi": _describe(f, old, new, "hi")}})
    feasible = after == "eligible" and not any(i["kind"] == "fixed" for i in items)
    return {"status": before, "already_eligible": False, "changes": items, "after": after, "cost": round(cost, 1),
            "feasible": feasible}


async def for_scheme(db: AsyncIOMotorDatabase, slug: str, profile: dict[str, Any]) -> dict[str, Any]:
    s = await db.schemes.find_one({"slug": slug, "deleted_at": None})
    if not s:
        raise ServiceError("scheme_not_found", 404)
    return {"scheme": slug, "name": s["name"], **whatif(s["rules"], profile)}


async def closest(db: AsyncIOMotorDatabase, profile: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    """Schemes that are not yet 'likely eligible' but would be after the smallest feasible change."""
    out = []
    for s in await schemes.list_schemes(db):
        if s["level"] == "state" and profile.get("state") not in (None, s["state"]):
            continue
        w = whatif(s["rules"], profile)
        if not w["already_eligible"] and w["feasible"] and w["changes"]:
            out.append({"scheme": s["slug"], "name": s["name"], "benefit": s["benefit"], **w})
    out.sort(key=lambda x: x["cost"])
    return out[:limit]
