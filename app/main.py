"""Application factory: FastAPI API + NiceGUI front-end in one process.

    uvicorn app.server:app --host 0.0.0.0 --port 8080   (see app/server.py)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db as database
from app.config import get_settings
from app.logging_conf import configure_logging
from app.routers import admin, applications, assistant, auth, documents, features, me, officer, schemes
from app.security import rate_limit
from app.services.errors import ServiceError

log = logging.getLogger("scheme_finder")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    db = database.connect(settings)
    await database.ensure_indexes(db)
    if settings.seed_on_startup and await db.users.count_documents({}) == 0:
        from app.seed import seed

        await seed(db)
    if settings.app_env != "test":
        import asyncio

        from app.ai import index

        # Build the retrieval index in the background so start-up stays fast.
        app.state.index_task = asyncio.create_task(index.rebuild(db))
    log.info("Scheme Finder started (env=%s, db=%s)", settings.app_env,
             "in-memory" if settings.uses_mock_db else settings.mongo_db)
    yield
    database.close()


def create_api() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    api = FastAPI(title="Scheme Finder API", version="0.2.0", lifespan=lifespan,
                  description="Prototype, not an official government service.")

    api.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"],
    )

    @api.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path.startswith("/api/"):
            ok, retry = rate_limit.limiter.hit(f"global:{rate_limit.client_ip(request)}",
                                               settings.rate_limit_global_per_minute)
            if not ok:
                return JSONResponse({"detail": {"code": "rate_limited", "retry_after": retry}}, status_code=429,
                                    headers={"Retry-After": str(retry)})
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @api.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse({"detail": {"code": exc.code, **exc.extra}}, status_code=exc.status)

    @api.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse({"detail": {"code": "validation_error", "errors": errors}}, status_code=422)

    @api.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error: %s", type(exc).__name__)
        return JSONResponse({"detail": {"code": "server_error"}}, status_code=500)

    @api.get("/api/health", tags=["meta"])
    async def health() -> dict[str, str]:
        await database.get_db().command("ping")
        return {"status": "ok", "notice": "Prototype, not an official government service."}

    api.include_router(auth.router)
    api.include_router(schemes.router)
    api.include_router(me.router)
    api.include_router(documents.router)
    api.include_router(assistant.router)
    api.include_router(applications.router)
    api.include_router(officer.router)
    api.include_router(features.router)
    api.include_router(admin.router)
    return api


def create_app() -> FastAPI:
    api = create_api()
    from app.ui import mount

    mount(api)
    return api
