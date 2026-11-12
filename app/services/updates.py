"""What's new / change tracker (spec 3.10).

* Feed: every scheme version after the first, compared with the version before it, summarised
  deterministically in plain language ("Income limit changed from ₹2,00,000 to ₹2,50,000").
* Affected users: when a scheme's rules change, the rules engine is re-run on every saved profile;
  anyone whose result changes gets a notification ("Your saved results may be affected").
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services import eligibility

STATUS = {"eligible": {"en": "likely eligible", "hi": "संभवतः पात्र"}, "maybe": {"en": "maybe", "hi": "शायद"},
          "not_eligible": {"en": "not eligible", "hi": "पात्र नहीं"}}


def summarise(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    o = {leaf["field"]: leaf for leaf in eligibility.iter_leaves(old.get("rules", {"all": []}))}
    n = {leaf["field"]: leaf for leaf in eligibility.iter_leaves(new.get("rules", {"all": []}))}
    fv = eligibility.format_value
    for f, leaf in n.items():
        label = eligibility.FIELDS[f]
        if f not in o:
            out.append({"en": f"New rule: {eligibility.describe_rule(leaf, 'en')}",
                        "hi": f"नया नियम: {eligibility.describe_rule(leaf, 'hi')}"})
            continue
        a = o[f]
        if (a["op"], a.get("value")) == (leaf["op"], leaf.get("value")):
            continue
        if a["op"] == leaf["op"] and isinstance(a.get("value"), (int, float)) and isinstance(leaf.get("value"), (int, float)):
            out.append({"en": f"{label.en} limit changed from {fv(f, a['value'], 'en')} to {fv(f, leaf['value'], 'en')}",
                        "hi": f"{label.hi} की सीमा {fv(f, a['value'], 'hi')} से बदलकर {fv(f, leaf['value'], 'hi')} हुई"})
        else:
            out.append({"en": f"Rule changed: {eligibility.describe_rule(a, 'en')} → {eligibility.describe_rule(leaf, 'en')}",
                        "hi": f"नियम बदला: {eligibility.describe_rule(a, 'hi')} → {eligibility.describe_rule(leaf, 'hi')}"})
    for f, leaf in o.items():
        if f not in n:
            out.append({"en": f"Rule removed: {eligibility.describe_rule(leaf, 'en')}",
                        "hi": f"नियम हटाया गया: {eligibility.describe_rule(leaf, 'hi')}"})
    if old.get("benefit") != new.get("benefit") and new.get("benefit"):
        out.append({"en": f"Benefit now: {new['benefit']['en']}", "hi": f"अब लाभ: {new['benefit']['hi']}"})
    added = set(new.get("documents", [])) - set(old.get("documents", []))
    removed = set(old.get("documents", [])) - set(new.get("documents", []))
    if added or removed:
        out.append({"en": f"Documents changed (+{len(added)} / −{len(removed)})",
                    "hi": f"दस्तावेज़ बदले (+{len(added)} / −{len(removed)})"})
    return out


async def feed(db: AsyncIOMotorDatabase, limit: int = 50) -> list[dict[str, Any]]:
    out = []
    async for v in db.scheme_versions.find({"version": {"$gt": 1}}).sort("changed_at", -1).limit(limit):
        prev = await db.scheme_versions.find_one({"slug": v["slug"], "version": v["version"] - 1})
        if not prev:
            continue
        changes = summarise(prev["data"], v["data"])
        out.append({"slug": v["slug"], "name": v["data"]["name"], "version": v["version"], "at": v["changed_at"],
                    "note": v.get("change_note"), "changes": changes or [{"en": "Text updated", "hi": "पाठ अपडेट हुआ"}]})
    return out


async def notify_affected(db: AsyncIOMotorDatabase, slug: str, old_rules: dict[str, Any] | None,
                          new_rules: dict[str, Any], name: dict[str, str]) -> int:
    if not old_rules or old_rules == new_rules:
        return 0
    count = 0
    async for p in db.profiles.find({"eligibility": {"$exists": True, "$ne": {}}}):
        before = eligibility.evaluate(old_rules, p["eligibility"]).status
        after = eligibility.evaluate(new_rules, p["eligibility"]).status
        if before == after:
            continue
        await db.notifications.insert_one({
            "_id": uuid.uuid4().hex, "user_id": p["user_id"], "application_id": None, "status": "rules_changed",
            "link": f"/scheme/{slug}", "read": False, "created_at": datetime.now(UTC), "seq": time.time_ns(),
            "title": {"en": f"{name['en']}: rules changed — your result may be affected",
                      "hi": f"{name['hi']}: नियम बदले — आपका नतीजा बदल सकता है"},
            "note": f"{STATUS[before]['en']} → {STATUS[after]['en']}"})
        count += 1
    return count
