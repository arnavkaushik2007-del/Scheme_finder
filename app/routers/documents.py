"""/api/documents (stored, encrypted) and /api/extract (in memory, nothing stored)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, Response, UploadFile, status
from fastapi.responses import Response as RawResponse

from app.config import get_settings
from app.db import get_db
from app.models.profile import DOC_TYPES, ConfirmFieldsIn
from app.security import rate_limit
from app.security.deps import UserOnly
from app.services import documents
from app.services.errors import ServiceError

router = APIRouter(prefix="/api", tags=["documents"])


async def _read_limited(file: UploadFile) -> bytes:
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ServiceError("file_too_large", 413, max_mb=get_settings().max_upload_mb)
    return data


@router.get("/documents")
async def list_documents(user: UserOnly) -> list[dict[str, Any]]:
    return await documents.list_docs(get_db(), user.id)


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(user: UserOnly, request: Request, doc_type: Annotated[DOC_TYPES, Form()],
                          file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    rate_limit.enforce(request, "upload", 20)
    return await documents.upload(get_db(), user.id, doc_type, await _read_limited(file), file.filename)


@router.post("/extract")
async def extract_only(user: UserOnly, request: Request, doc_type: Annotated[DOC_TYPES, Form()],
                       file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    """'Use once' mode: read the fields from a file in memory. Nothing is written to disk or the database."""
    rate_limit.enforce(request, "upload", 20)
    data = await _read_limited(file)
    ctype = documents.validate_upload(data, file.filename)
    return await documents.run_extraction(data, ctype, doc_type)


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, user: UserOnly) -> dict[str, Any]:
    return await documents.get_doc(get_db(), user.id, doc_id)


@router.get("/documents/{doc_id}/file")
async def get_document_file(doc_id: str, user: UserOnly) -> Response:
    data, ctype = await documents.read_file(get_db(), user.id, doc_id)
    return RawResponse(content=data, media_type=ctype, headers={
        "Content-Disposition": "inline", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.put("/documents/{doc_id}/file")
async def replace_document_file(doc_id: str, user: UserOnly, request: Request,
                                file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    rate_limit.enforce(request, "upload", 20)
    return await documents.replace(get_db(), user.id, doc_id, await _read_limited(file), file.filename)


@router.post("/documents/{doc_id}/confirm")
async def confirm_document(doc_id: str, body: ConfirmFieldsIn, user: UserOnly) -> dict[str, Any]:
    return await documents.confirm(get_db(), user.id, doc_id, body.fields)


@router.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(doc_id: str, user: UserOnly) -> None:
    await documents.delete(get_db(), user.id, doc_id)
