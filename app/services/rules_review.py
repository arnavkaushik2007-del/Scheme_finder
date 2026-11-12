"""Rules-review queue: extracted rules wait here for an admin's approval before going live.

Approving creates a new scheme version through the normal versioned update (so applications keep
the version they were judged under) and is audited; rejecting needs a reason.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.ai import rule_extraction
from app.models.scheme import SchemeUpdateIn
from app.services import audit, eligibility, schemes
from app.services.errors import ServiceError


def _now() -> datetime:
    return datetime.now(UTC)


def _diff(current: dict[str, Any] | None, proposed: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    cur = {l["field"]: l for l in eligibility.iter_leaves(current)} if current else {}
    new = {l["field"]: l for l in eligibility.iter_leaves(proposed)} if proposed else {}
    text = lambda l: {"en": eligibility.describe_rule(l, "en"), "hi": eligibility.describe_rule(l, "hi")}  # noqa: E731
    return {
        "added": [text(new[f]) for f in new if f not in cur],
        "removed": [text(cur[f]) for f in cur if f not in new],
        "changed": [{"from": text(cur[f]), "to": text(new[f])} for f in new
                    if f in cur and (cur[f]["op"], cur[f].get("value")) != (new[f]["op"], new[f].get("value"))],
        "unchanged": [text(new[f]) for f in new if f in cur and (cur[f]["op"], cur[f].get("value")) == (new[f]["op"], new[f].get("value"))],
    }


def guideline_text(data: bytes | None, text: str | None) -> tuple[str, str]:
    if data:
        if not data.startswith(b"%PDF-"):
            raise ServiceError("unsupported_file_type", 415, allowed="PDF")
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if pdf.page_count > 60:
                raise ServiceError("too_many_pages", 422, max_pages=60)
            body = "\n".join(p.get_text() for p in pdf)
        if len(body.strip()) < 30:
            raise ServiceError("no_text_found", 422)
        return body, hashlib.sha256(data).hexdigest()
    if not text or len(text.strip()) < 30:
        raise ServiceError("no_text_found", 422)
    return text, hashlib.sha256(text.encode()).hexdigest()


async def ingest(db: AsyncIOMotorDatabase, slug: str, body: str, sha: str, filename: str | None,
                 actor: dict[str, Any]) -> dict[str, Any]:
    scheme = await db.schemes.find_one({"slug": slug, "deleted_at": None})
    if not scheme:
        raise ServiceError("scheme_not_found", 404)
    ex = await rule_extraction.extract(body)
    if not ex["rules"]:
        raise ServiceError("no_rules_found", 422, dropped=ex["dropped"])
    pid = uuid.uuid4().hex
    proposal = {
        "_id": pid, "scheme_slug": slug, "scheme_name": scheme["name"], "base_version": scheme["version"],
        "current_rules": scheme["rules"], "proposed_rules": ex["rules"], "evidence": ex["evidence"],
        "dropped": ex["dropped"], "method": ex["method"], "diff": _diff(scheme["rules"], ex["rules"]),
        "source": {"filename": filename, "sha256": sha, "chars": len(body)},
        "status": "pending", "created_by": actor["id"], "created_at": _now(),
    }
    await db.rule_proposals.insert_one(proposal)
    await audit.record(db, actor=actor, action="rules.proposed", target={"type": "scheme", "id": slug},
                       after={"proposal": pid, "method": ex["method"], "rules": ex["rules"]})
    return public(proposal)


def public(p: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in p.items() if k != "_id"}
    out["id"] = p["_id"]
    return out


async def list_proposals(db: AsyncIOMotorDatabase, status: str | None) -> list[dict[str, Any]]:
    q = {"status": status} if status else {}
    return [public(p) async for p in db.rule_proposals.find(q).sort("created_at", -1).limit(100)]


async def decide(db: AsyncIOMotorDatabase, pid: str, approve: bool, reason: str, rules: dict[str, Any] | None,
                 actor: dict[str, Any]) -> dict[str, Any]:
    p = await db.rule_proposals.find_one({"_id": pid, "status": "pending"})
    if not p:
        raise ServiceError("not_found", 404)
    if not approve:
        await db.rule_proposals.update_one({"_id": pid}, {"$set": {
            "status": "rejected", "decided_by": actor["id"], "decided_at": _now(), "decision_reason": reason}})
        await audit.record(db, actor=actor, action="rules.rejected", target={"type": "scheme", "id": p["scheme_slug"]},
                           reason=reason, after={"proposal": pid})
        return public(await db.rule_proposals.find_one({"_id": pid}))
    final_rules = rules or p["proposed_rules"]
    try:
        eligibility.validate_rules(final_rules)
    except eligibility.RuleError as exc:
        raise ServiceError("invalid_rules", 422, message=str(exc)) from exc
    current = await db.schemes.find_one({"slug": p["scheme_slug"], "deleted_at": None})
    if not current:
        raise ServiceError("scheme_not_found", 404)
    data = {k: current[k] for k in SchemeUpdateIn.model_fields if k in current and k != "change_note"}
    data.update({"rules": final_rules, "change_note": f"Rules from guideline review {pid[:8]}: {reason}"[:500]})
    updated = await schemes.update_scheme(db, p["scheme_slug"], SchemeUpdateIn.model_validate(data), actor)
    await db.rule_proposals.update_one({"_id": pid}, {"$set": {
        "status": "approved", "decided_by": actor["id"], "decided_at": _now(), "decision_reason": reason,
        "approved_rules": final_rules, "new_version": updated["version"],
        "edited_by_admin": rules is not None and rules != p["proposed_rules"]}})
    await audit.record(db, actor=actor, action="rules.approved", target={"type": "scheme", "id": p["scheme_slug"]},
                       reason=reason, after={"proposal": pid, "version": updated["version"]})
    return public(await db.rule_proposals.find_one({"_id": pid}))
