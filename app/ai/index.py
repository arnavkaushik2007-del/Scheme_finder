"""Builds the retrieval corpus from the stored scheme data and answers hybrid searches.

Every chunk comes from reviewed, stored content (scheme summary, rules, documents, steps, and the
About page) and carries its source link, so every answer can cite where it came from.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.ai.embeddings import get_embedder
from app.ai.text import tokens  # noqa: F401 - re-exported for guard/assistant
from app.ai.vectorstore import Hit, get_store
from app.services import eligibility

log = logging.getLogger(__name__)

STATE: dict[str, Any] = {"ready": False, "building": False, "chunks": 0, "built_at": None, "error": None}
_lock = asyncio.Lock()

GENERAL = [
    ("about-decisions", "/about",
     {"en": "How eligibility is decided: a fixed rules engine checks each rule such as age, income and category, "
            "and shows which rule matched, failed or is unknown. AI never makes the decision. A human officer "
            "gives the final approval. Approval is never guaranteed; always verify on the official portal.",
      "hi": "पात्रता कैसे तय होती है: एक तय नियम-इंजन हर नियम जैसे आयु, आय और वर्ग की जाँच करता है और बताता है कि कौन-सा "
            "नियम मिला, नहीं मिला या अज्ञात है। AI कभी निर्णय नहीं लेता। अंतिम मंज़ूरी अधिकारी देते हैं। मंज़ूरी की कोई "
            "गारंटी नहीं है; हमेशा आधिकारिक पोर्टल पर जाँचें।"}),
    ("about-privacy", "/about",
     {"en": "Privacy: Scheme Finder never stores a full Aadhaar number, only the last four digits. Uploaded files "
            "are encrypted. You can withdraw consent or delete your data at any time from your profile.",
      "hi": "गोपनीयता: योजना खोजक पूरा आधार नंबर कभी नहीं रखता, केवल अंतिम चार अंक। अपलोड की गई फ़ाइलें एन्क्रिप्ट "
            "रहती हैं। आप कभी भी अपनी प्रोफ़ाइल से सहमति वापस ले सकते हैं या डेटा मिटा सकते हैं।"}),
    ("about-fees", "/about",
     {"en": "Government schemes do not charge a fee to apply through official channels and never ask for your "
            "OTP, PIN or bank password. Messages asking for money to 'release' a benefit are likely scams.",
      "hi": "सरकारी योजनाओं में आधिकारिक माध्यम से आवेदन के लिए कोई शुल्क नहीं लगता और कभी आपका OTP, PIN या बैंक "
            "पासवर्ड नहीं माँगा जाता। लाभ 'जारी' करने के लिए पैसे माँगने वाले संदेश संभवतः धोखाधड़ी हैं।"}),
]


def scheme_chunks(s: dict[str, Any], catalog: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    out = []
    leaves = list(eligibility.iter_leaves(s["rules"]))
    for lang in ("en", "hi"):
        name = s["name"][lang]
        level = ("Central scheme" if s["level"] == "central" else f"State scheme ({s['state']})") if lang == "en" \
            else ("केंद्रीय योजना" if s["level"] == "central" else f"राज्य योजना ({s['state']})")
        rules = "; ".join(eligibility.describe_rule(r, lang) + (f" ({r['note'][lang]})" if r.get("note") else "")
                          for r in leaves)
        docs = "; ".join(f"{catalog.get(d, {}).get('name', {}).get(lang, d)}"
                         + (f" — {catalog[d]['where'][lang].rstrip('।. ')}" if d in catalog else "") for d in s["documents"])
        steps = " ".join(f"{i}. {st[lang]}" for i, st in enumerate(s["steps"], 1))
        L = {"en": ("Benefit", "Ministry", "Eligibility rules for", "Documents needed for", "How to apply for",
                    "Note: in this tool the rules are a simplified sample; verify on the official portal."),
             "hi": ("लाभ", "मंत्रालय", "पात्रता नियम —", "ज़रूरी दस्तावेज़ —", "आवेदन कैसे करें —",
                    "ध्यान दें: इस टूल में नियम सरल नमूना हैं; आधिकारिक पोर्टल पर जाँचें।")}[lang]
        sections = {
            "overview": f"{name}. {s['summary'][lang]} {L[0]}: {s['benefit'][lang]}. {L[1]}: {s['ministry'][lang]}. {level}.",
            "rules": f"{L[2]} {name}: {rules}. {L[5]}",
            "documents": f"{L[3]} {name}: {docs}.",
            "steps": f"{L[4]} {name}: {steps}",
        }
        for section, text in sections.items():
            out.append((f"{s['slug']}:{section}:{lang}", text, {
                "slug": s["slug"], "section": section, "lang": lang, "url": s["official_url"],
                "title_en": s["name"]["en"], "title_hi": s["name"]["hi"], "version": s.get("version", 1)}))
    return out


async def rebuild(db: AsyncIOMotorDatabase) -> int:
    async with _lock:
        STATE.update(building=True, error=None)
        try:
            catalog = {d["_id"]: d async for d in db.required_documents.find()}
            rows: list[tuple[str, str, dict[str, Any]]] = []
            async for s in db.schemes.find({"deleted_at": None}):
                rows += scheme_chunks(s, catalog)
            for cid, url, text in GENERAL:
                for lang in ("en", "hi"):
                    rows.append((f"general:{cid}:{lang}", text[lang], {
                        "slug": "", "section": cid, "lang": lang, "url": url,
                        "title_en": "About Scheme Finder", "title_hi": "योजना खोजक के बारे में", "version": 1}))
            embedder = await asyncio.to_thread(get_embedder)  # first load can take seconds
            vectors = await asyncio.to_thread(embedder.embed_passages, [r[1] for r in rows])
            store = get_store()
            await asyncio.to_thread(store.reset)
            await asyncio.to_thread(store.add, [r[0] for r in rows], [r[1] for r in rows], vectors,
                                    [r[2] for r in rows])
            STATE.update(ready=True, chunks=len(rows), built_at=datetime.now(UTC).isoformat(),
                         embedder=embedder.name, store=store.name)
            log.info("retrieval index built: %d chunks (%s, %s)", len(rows), embedder.name, store.name)
            return len(rows)
        except Exception as exc:  # noqa: BLE001
            STATE["error"] = type(exc).__name__
            log.exception("index build failed")
            raise
        finally:
            STATE["building"] = False


async def ensure(db: AsyncIOMotorDatabase) -> None:
    if not STATE["ready"] and not STATE["building"]:
        await rebuild(db)
    while STATE["building"]:
        await asyncio.sleep(0.2)


async def search(db: AsyncIOMotorDatabase, query: str, *, k: int = 6, slug: str | None = None,
                 lang: str | None = None) -> list[Hit]:
    """Hybrid search: embedding similarity (main signal) plus keyword overlap (tie-breaker)."""
    await ensure(db)
    embedder = await asyncio.to_thread(get_embedder)
    qv = await asyncio.to_thread(embedder.embed_query, query)
    where: dict[str, Any] = {}
    if slug:
        where["slug"] = slug
    hits = await asyncio.to_thread(get_store().query, qv, 40, where or None)
    qt = set(tokens(query))
    for h in hits:
        ht = set(tokens(h.text))
        kw = len(qt & ht) / len(qt) if qt else 0.0
        h.meta = {**h.meta, "vector_score": round(h.score, 4), "keyword_score": round(kw, 3)}
        h.score = 0.8 * h.score + 0.2 * kw + (0.02 if lang and h.meta.get("lang") == lang else 0.0)
    hits.sort(key=lambda h: -h.score)
    return hits[:k]


async def search_schemes(db: AsyncIOMotorDatabase, query: str, k: int = 30) -> list[tuple[str, float]]:
    """Meaning-based scheme search for /explore: best chunk score per scheme."""
    hits = await search(db, query, k=120)
    best: dict[str, float] = {}
    floor = get_embedder().abstain_below
    for h in hits:
        slug = h.meta.get("slug")
        if slug and h.meta.get("vector_score", 0) >= floor:
            best[slug] = max(best.get(slug, 0.0), h.score)
    return sorted(best.items(), key=lambda kv: -kv[1])[:k]
