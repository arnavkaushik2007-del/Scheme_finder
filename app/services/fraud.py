"""Fake-account detection (spec 0.5): explainable rules + an Isolation Forest anomaly model.

Rules (each gives a reason the admin can read):
* duplicate_file      - a document file (same SHA-256) also uploaded by another account
* shared_identity     - same name + date of birth, or same Aadhaar last-4 + date of birth, as another account
* impossible_values   - e.g. declared age far from the age on the date of birth, widowed under 18, family of 40
* upload_burst        - many documents uploaded within ten minutes
* many_applications   - unusually many applications

Anomaly model: Isolation Forest (scikit-learn) over per-account numeric features, trained on all
current citizen accounts each time the scan runs. It only adds a "statistically unusual" reason;
it never acts by itself. The admin makes the final call (suspend, delete, or clear the flag).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.ai.extraction import age_from_dob
from app.services import audit
from app.services.errors import ServiceError

log = logging.getLogger(__name__)

MODEL = "rules-v1 + isolation-forest-v1"
FEATURES = ["n_docs", "n_apps", "dup_files", "shared_identity", "max_uploads_10min", "account_age_days",
            "profile_fields", "impossible"]


def _aware(dt: Any) -> datetime | None:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _impossible(elig: dict[str, Any], personal: dict[str, Any]) -> list[str]:
    out = []
    dob, age = personal.get("date_of_birth"), elig.get("age")
    if dob and age is not None:
        real = age_from_dob(dob)
        if real is not None and abs(real - age) > 2:
            out.append(f"declared age {age} but date of birth gives {real}")
    if age is not None and age < 18 and elig.get("marital_status") == "widowed":
        out.append("widowed under 18")
    if (elig.get("family_size") or 0) > 25:
        out.append(f"family size {elig['family_size']}")
    if (elig.get("land_holding_hectares") or 0) > 500:
        out.append(f"land holding {elig['land_holding_hectares']} ha")
    if age is not None and age < 14 and elig.get("occupation") in ("farmer", "salaried", "retired"):
        out.append(f"age {age} with occupation {elig['occupation']}")
    return out


async def _features(db: AsyncIOMotorDatabase) -> dict[str, dict[str, Any]]:
    users = {u["_id"]: u async for u in db.users.find({"role": "user", "deleted_at": None})}
    hash_owners: dict[str, set[str]] = defaultdict(set)
    uploads: dict[str, list[datetime]] = defaultdict(list)
    n_docs: dict[str, int] = defaultdict(int)
    async for d in db.documents.find({"file_hash": {"$exists": True}}):
        hash_owners[d["file_hash"]].add(d["user_id"])
        if d.get("deleted_at") is None:
            n_docs[d["user_id"]] += 1
        if (ts := _aware(d.get("created_at"))):
            uploads[d["user_id"]].append(ts)
    profiles = {p["user_id"]: p async for p in db.profiles.find()}
    identity: dict[tuple[str, str], set[str]] = defaultdict(set)
    for uid, p in profiles.items():
        per = p.get("personal") or {}
        if per.get("date_of_birth"):
            if per.get("full_name"):
                identity[("name", per["full_name"].strip().lower() + "|" + per["date_of_birth"])].add(uid)
            if per.get("aadhaar_last4"):
                identity[("aadhaar", per["aadhaar_last4"] + "|" + per["date_of_birth"])].add(uid)
    now = datetime.now(UTC)
    out: dict[str, dict[str, Any]] = {}
    for uid, u in users.items():
        p = profiles.get(uid) or {}
        elig, per = p.get("eligibility") or {}, p.get("personal") or {}
        dups = [h for h, owners in hash_owners.items() if uid in owners and len(owners) > 1]
        shared = {o for key, owners in identity.items() if uid in owners for o in owners} - {uid}
        times = sorted(uploads.get(uid, []))
        burst = max((sum(1 for t2 in times if t <= t2 <= t + timedelta(minutes=10)) for t in times), default=0)
        imp = _impossible(elig, per)
        out[uid] = {
            "n_docs": n_docs.get(uid, 0), "n_apps": await db.applications.count_documents({"user_id": uid}),
            "dup_files": len(dups), "shared_identity": len(shared), "shared_with": sorted(shared),
            "max_uploads_10min": burst,
            "account_age_days": round((now - (_aware(u.get("created_at")) or now)).total_seconds() / 86400, 2),
            "profile_fields": len(elig) + len(per), "impossible": len(imp), "impossible_detail": imp,
        }
    return out


def _rule_reasons(f: dict[str, Any]) -> list[dict[str, Any]]:
    r = []
    if f["dup_files"]:
        r.append({"code": "duplicate_file", "weight": 0.5,
                  "en": f"{f['dup_files']} uploaded file(s) also appear in another account.",
                  "hi": f"{f['dup_files']} अपलोड फ़ाइल किसी दूसरे खाते में भी है।"})
    if f["shared_identity"]:
        r.append({"code": "shared_identity", "weight": 0.5,
                  "en": f"Same name/Aadhaar-last-4 and date of birth as {f['shared_identity']} other account(s).",
                  "hi": f"नाम/आधार-अंतिम-4 और जन्म तिथि {f['shared_identity']} अन्य खाते(खातों) जैसी है।"})
    if f["impossible"]:
        r.append({"code": "impossible_values", "weight": 0.4, "en": "Impossible values: " + "; ".join(f["impossible_detail"]),
                  "hi": "असंभव मान: " + "; ".join(f["impossible_detail"])})
    if f["max_uploads_10min"] >= 8:
        r.append({"code": "upload_burst", "weight": 0.25,
                  "en": f"{f['max_uploads_10min']} documents uploaded within 10 minutes.",
                  "hi": f"10 मिनट में {f['max_uploads_10min']} दस्तावेज़ अपलोड किए।"})
    if f["n_apps"] >= 10:
        r.append({"code": "many_applications", "weight": 0.2, "en": f"{f['n_apps']} applications.",
                  "hi": f"{f['n_apps']} आवेदन।"})
    return r


def _anomaly(features: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ids = list(features)
    if len(ids) < 8:
        return {uid: {"available": False, "reason": "too few accounts to model (need 8)"} for uid in ids}
    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        return {uid: {"available": False, "reason": "scikit-learn not installed"} for uid in ids}
    import numpy as np

    X = np.array([[float(features[u][k]) for k in FEATURES] for u in ids])
    model = IsolationForest(n_estimators=200, contamination="auto", random_state=42).fit(X)
    scores = -model.score_samples(X)  # higher = more unusual
    preds = model.predict(X)
    lo, hi = float(scores.min()), float(scores.max())
    return {u: {"available": True, "score": round((float(s) - lo) / ((hi - lo) or 1), 3), "is_outlier": bool(p == -1)}
            for u, s, p in zip(ids, scores, preds, strict=True)}


async def scan(db: AsyncIOMotorDatabase, actor: dict[str, Any]) -> dict[str, Any]:
    feats = await _features(db)
    anomaly = _anomaly(feats)
    flagged = 0
    for uid, f in feats.items():
        reasons = _rule_reasons(f)
        a = anomaly[uid]
        if a.get("available") and a["is_outlier"] and a["score"] >= 0.8:
            reasons.append({"code": "statistical_outlier", "weight": 0.2,
                            "en": f"Statistically unusual account (anomaly score {a['score']}).",
                            "hi": f"सांख्यिकीय रूप से असामान्य खाता (स्कोर {a['score']})।"})
        score = round(min(1.0, sum(r["weight"] for r in reasons)), 3)
        existing = await db.fraud_flags.find_one({"user_id": uid})
        if score >= 0.4:
            flagged += 1
            if existing and existing.get("cleared") and existing.get("score", 0) >= score:
                continue  # admin already reviewed and cleared this level of evidence
            await db.fraud_flags.update_one({"user_id": uid}, {"$set": {
                "user_id": uid, "score": score, "reasons": reasons, "anomaly": a, "features": f, "model": MODEL,
                "cleared": False, "updated_at": datetime.now(UTC)}, "$setOnInsert": {"created_at": datetime.now(UTC)}},
                upsert=True)
        elif existing and not existing.get("cleared"):
            await db.fraud_flags.update_one({"user_id": uid}, {"$set": {"cleared": True, "resolution": "no_longer_flagged",
                                                                       "updated_at": datetime.now(UTC)}})
    await audit.record(db, actor=actor, action="fraud.scan", after={"accounts": len(feats), "flagged": flagged,
                                                                    "model": MODEL})
    return {"accounts": len(feats), "flagged": flagged, "model": MODEL,
            "anomaly_model": next(iter(anomaly.values()), {"available": False}).get("available", False)}


async def clear(db: AsyncIOMotorDatabase, user_id: str, reason: str, actor: dict[str, Any]) -> None:
    res = await db.fraud_flags.update_one({"user_id": user_id, "cleared": False},
                                          {"$set": {"cleared": True, "resolution": "cleared_by_admin", "clear_reason": reason,
                                                    "updated_at": datetime.now(UTC)}})
    if res.matched_count == 0:
        raise ServiceError("not_found", 404)
    await audit.record(db, actor=actor, action="fraud.clear", target={"type": "user", "id": user_id}, reason=reason)
