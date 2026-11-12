"""Benefit stacking planner (spec 3.7): best combination, exclusion warnings, timeline, one-page PDF.

The best combination is an exact maximum-weight independent set over the "cannot be combined"
graph: each group of mutually exclusive schemes is small, so every subset is checked. The timeline
orders the chosen schemes by deadline, then by how many required documents are already uploaded.
"""

from __future__ import annotations

import itertools
from datetime import date
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services import eligibility, schemes


def best_combination(items: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    by = {i["slug"]: i for i in items}
    adj: dict[str, set[str]] = {s: set() for s in by}
    for s, i in by.items():
        for o in i.get("exclusive_with", []):
            if o in by:
                adj[s].add(o)
                adj[o].add(s)
    seen: set[str] = set()
    chosen: list[str] = []
    conflicts: list[dict[str, Any]] = []
    for start in by:
        if start in seen:
            continue
        comp, stack = [], [start]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.append(x)
            stack.extend(adj[x] - seen)
        if len(comp) == 1:
            chosen.append(comp[0])
            continue
        best, best_val = [], -1
        if len(comp) <= 16:
            for r in range(len(comp), 0, -1):
                for subset in itertools.combinations(comp, r):
                    if any(b in adj[a] for a, b in itertools.combinations(subset, 2)):
                        continue
                    val = sum(by[s]["annual_benefit_inr"] for s in subset)
                    if val > best_val:
                        best, best_val = list(subset), val
        else:  # very large conflict group: greedy by value
            for s in sorted(comp, key=lambda s: -by[s]["annual_benefit_inr"]):
                if not adj[s] & set(best):
                    best.append(s)
        chosen += best
        for a, b in itertools.combinations(comp, 2):
            if b in adj[a]:
                keep, drop = (a, b) if a in best else (b, a)
                conflicts.append({"keep": keep, "drop": drop, "keep_name": by[keep]["name"], "drop_name": by[drop]["name"],
                                  "en": f"You cannot take both {by[a]['name']['en']} and {by[b]['name']['en']}; "
                                        f"the plan keeps {by[keep]['name']['en']} (higher benefit).",
                                  "hi": f"आप {by[a]['name']['hi']} और {by[b]['name']['hi']} दोनों नहीं ले सकते; "
                                        f"योजना में {by[keep]['name']['hi']} रखी गई है (अधिक लाभ)।"})
    return chosen, conflicts


async def plan(db: AsyncIOMotorDatabase, profile: dict[str, Any], user_id: str | None,
               include_maybe: bool = False) -> dict[str, Any]:
    clean = eligibility.clean_profile(profile)
    rows = await schemes.list_schemes(db)
    items = []
    for s in rows:
        if s["level"] == "state" and clean.get("state") not in (None, s["state"]):
            continue
        st = eligibility.evaluate(s["rules"], clean).status
        if st == "eligible" or (include_maybe and st == "maybe"):
            items.append({**s, "status": st})
    chosen, conflicts = best_combination(items)
    have = {d["doc_type"] async for d in db.documents.find({"user_id": user_id, "deleted_at": None})} if user_id else set()
    catalog = {d["_id"]: d["name"] async for d in db.required_documents.find()}
    timeline = []
    for s in (i for i in items if i["slug"] in chosen):
        docs = [{"id": d, "name": catalog.get(d, {"en": d, "hi": d}),
                 "have": d in have or (d == "bpl_card" and "ration_card" in have)} for d in s.get("documents", [])]
        timeline.append({"slug": s["slug"], "name": s["name"], "benefit": s["benefit"], "status": s["status"],
                         "annual_benefit_inr": s["annual_benefit_inr"], "deadline": s.get("deadline"),
                         "official_url": s["official_url"], "documents": docs,
                         "missing": sum(1 for d in docs if not d["have"])})
    timeline.sort(key=lambda x: (x["deadline"] or "9999-12-31", x["missing"], -x["annual_benefit_inr"]))
    for i, x in enumerate(timeline, 1):
        x["order"] = i
    return {"total_annual_inr": sum(x["annual_benefit_inr"] for x in timeline), "timeline": timeline,
            "conflicts": conflicts, "considered": len(items),
            "note": {"en": "Estimate only. Insurance cover and loans are counted at face value; verify on the official portals.",
                     "hi": "केवल अनुमान। बीमा कवर और ऋण पूरे मूल्य पर गिने गए हैं; आधिकारिक पोर्टल पर जाँचें।"}}


def pdf(p: dict[str, Any], name: str) -> bytes:
    """One-page plan as PDF (English; PDF base fonts have no Devanagari glyphs)."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    green = (0.06, 0.3, 0.21)
    y = 50

    def line(text: str, size: float = 10, color: tuple[float, float, float] = (0.1, 0.1, 0.1), dy: float = 15,
             font: str = "helv") -> None:
        nonlocal y
        if y > 800:
            return
        page.insert_text((40, y), text[:110], fontsize=size, fontname=font, color=color)
        y += dy

    line("Scheme Finder - my benefit plan", 18, green, 24, "hebo")
    line(f"For: {name or 'Citizen'}   Date: {date.today().isoformat()}", 10, (0.35, 0.35, 0.35))
    line("Prototype, not an official government service. Always verify on the official portal.", 9, (0.7, 0.1, 0.1), 22)
    line(f"Estimated yearly benefit: Rs {p['total_annual_inr']:,}", 12, green, 22, "hebo")
    for x in p["timeline"]:
        line(f"{x['order']}. {x['name']['en']}", 11, (0, 0, 0), 14, "hebo")
        line(f"   {x['benefit']['en']}", 9)
        if x["deadline"]:
            line(f"   Deadline: {x['deadline']}", 9)
        missing = [d["name"]["en"] for d in x["documents"] if not d["have"]]
        line("   Documents still needed: " + (", ".join(missing) if missing else "none - all uploaded"), 9)
        line(f"   Apply: {x['official_url']}", 9, (0.1, 0.3, 0.6), 18)
    if p["conflicts"]:
        line("Cannot be combined:", 11, (0.6, 0.3, 0), 14, "hebo")
        for c in p["conflicts"]:
            line("   " + c["en"], 9)
    line(p["note"]["en"], 8, (0.4, 0.4, 0.4))
    out = doc.tobytes()
    doc.close()
    return out
