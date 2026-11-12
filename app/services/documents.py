"""Citizen documents: validate -> encrypt -> store -> extract fields -> user review -> profile."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.ai import extraction
from app.config import get_settings
from app.services import profiles, storage
from app.services.errors import ServiceError

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_name(name: str | None) -> str:
    name = (name or "document").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:80]
    return extraction.mask_ids(name) or "document"


def validate_upload(data: bytes, filename: str | None) -> str:
    settings = get_settings()
    if not data:
        raise ServiceError("empty_file", 422)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ServiceError("file_too_large", 413, max_mb=settings.max_upload_mb)
    ctype = extraction.sniff_type(data)
    if ctype is None:
        raise ServiceError("unsupported_file_type", 415, allowed="PDF, PNG, JPEG, WEBP")
    if ctype.startswith("image/"):
        try:
            from PIL import Image
            import io

            with Image.open(io.BytesIO(data)) as img:
                img.verify()
        except Exception as exc:  # noqa: BLE001 - corrupt or decompression bomb
            raise ServiceError("corrupt_file", 422) from exc
    return ctype


async def run_extraction(data: bytes, ctype: str, doc_type: str) -> dict[str, Any]:
    try:
        ex = await extraction.extract(data, ctype, doc_type)
    except ValueError as exc:
        if str(exc) == "too_many_pages":
            raise ServiceError("too_many_pages", 422, max_pages=get_settings().max_pdf_pages) from exc
        raise ServiceError("corrupt_file", 422) from exc
    except Exception as exc:  # noqa: BLE001 - unreadable file
        log.warning("extraction failed: %s", type(exc).__name__)
        raise ServiceError("corrupt_file", 422) from exc
    return ex.to_dict()


def public(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc["_id"], "doc_type": doc["doc_type"], "filename": doc.get("filename"),
        "content_type": doc.get("content_type"), "size": doc.get("size"), "status": doc.get("status"),
        "extracted": doc.get("extracted"), "confirmed_fields": doc.get("confirmed_fields"),
        "created_at": doc.get("created_at"), "updated_at": doc.get("updated_at"), "version": doc.get("version", 1),
    }


async def upload(db: AsyncIOMotorDatabase, user_id: str, doc_type: str, data: bytes,
                 filename: str | None) -> dict[str, Any]:
    await profiles.require_save_consent(db, user_id)
    ctype = validate_upload(data, filename)
    extracted = await run_extraction(data, ctype, doc_type)
    file_hash = storage.sha256(data)
    # Same file uploaded by a different account -> flag for the admin's fake-account review (step 7).
    dup = await db.documents.find_one({"file_hash": file_hash, "user_id": {"$ne": user_id}}, {"_id": 1})
    doc = {
        "_id": uuid.uuid4().hex, "user_id": user_id, "doc_type": doc_type, "filename": _safe_name(filename),
        "content_type": ctype, "size": len(data), "file_hash": file_hash, "storage_key": storage.put(data),
        "encrypted": True, "extracted": extracted, "confirmed_fields": None,
        "status": "needs_review" if extracted["fields"] else "no_fields",
        "duplicate_across_accounts": bool(dup), "version": 1,
        "created_at": _now(), "updated_at": _now(), "deleted_at": None,
    }
    await db.documents.insert_one(doc)
    return public(doc)


async def _own(db: AsyncIOMotorDatabase, user_id: str, doc_id: str) -> dict[str, Any]:
    doc = await db.documents.find_one({"_id": doc_id, "user_id": user_id, "deleted_at": None})
    if not doc:
        raise ServiceError("document_not_found", 404)
    return doc


async def list_docs(db: AsyncIOMotorDatabase, user_id: str) -> list[dict[str, Any]]:
    return [public(d) async for d in db.documents.find({"user_id": user_id, "deleted_at": None}).sort("created_at", -1)]


async def get_doc(db: AsyncIOMotorDatabase, user_id: str, doc_id: str) -> dict[str, Any]:
    return public(await _own(db, user_id, doc_id))


async def read_file(db: AsyncIOMotorDatabase, user_id: str, doc_id: str) -> tuple[bytes, str]:
    doc = await _own(db, user_id, doc_id)
    try:
        return storage.get(doc["storage_key"]), doc["content_type"]
    except storage.StorageError as exc:
        raise ServiceError("file_unavailable", 410) from exc


async def replace(db: AsyncIOMotorDatabase, user_id: str, doc_id: str, data: bytes,
                  filename: str | None) -> dict[str, Any]:
    doc = await _own(db, user_id, doc_id)
    ctype = validate_upload(data, filename)
    extracted = await run_extraction(data, ctype, doc["doc_type"])
    new_key = storage.put(data)
    await db.documents.update_one({"_id": doc_id}, {"$set": {
        "filename": _safe_name(filename), "content_type": ctype, "size": len(data), "file_hash": storage.sha256(data),
        "storage_key": new_key, "extracted": extracted, "confirmed_fields": None,
        "status": "needs_review" if extracted["fields"] else "no_fields", "updated_at": _now(),
        "version": doc.get("version", 1) + 1}})
    storage.delete(doc.get("storage_key"))
    return public(await _own(db, user_id, doc_id))


async def delete(db: AsyncIOMotorDatabase, user_id: str, doc_id: str) -> None:
    doc = await _own(db, user_id, doc_id)
    storage.delete(doc.get("storage_key"))  # the file itself is removed; the row is a soft-deleted tombstone
    await db.documents.update_one({"_id": doc_id}, {"$set": {
        "deleted_at": _now(), "storage_key": None, "extracted": None, "confirmed_fields": None}})


async def confirm(db: AsyncIOMotorDatabase, user_id: str, doc_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """The user reviewed the extracted fields (and may have corrected them). Copy them to the profile."""
    doc = await _own(db, user_id, doc_id)
    allowed = set(extraction.DOC_FIELDS.get(doc["doc_type"], extraction.GENERIC_FIELDS)) | {"age"}
    clean = {k: v for k, v in fields.items() if k in allowed and v not in (None, "")}
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise ServiceError("unknown_fields", 422, fields=unknown)
    clean.pop("issue_date", None)  # kept on the document only
    if "date_of_birth" in clean and "age" not in clean:
        age = extraction.age_from_dob(str(clean["date_of_birth"]))
        if age is not None:
            clean["age"] = age
    profile = await profiles.merge_fields(db, user_id, clean, source=f"document:{doc_id}")
    await db.documents.update_one({"_id": doc_id}, {"$set": {
        "confirmed_fields": fields, "status": "confirmed", "updated_at": _now()}})
    return {"document": public(await _own(db, user_id, doc_id)), "profile": profile}
