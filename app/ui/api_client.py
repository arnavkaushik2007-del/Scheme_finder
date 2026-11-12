"""The UI talks to the backend only through the HTTP API (UI -> API -> DB -> UI).

Requests are sent in-process through httpx's ASGI transport, so they pass through exactly the same
routing, validation, role checks and rate limits as an external client. The real browser IP is
passed as the ASGI client address so rate limiting stays per person.

Tokens live in ``app.storage.user`` (server-side, keyed by a signed browser cookie).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI
from nicegui import app, ui

log = logging.getLogger(__name__)

_api: FastAPI | None = None


def bind(api: FastAPI) -> None:
    global _api
    _api = api


@dataclass
class ApiResult:
    status: int
    data: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def code(self) -> str:
        if isinstance(self.data, dict) and isinstance(self.data.get("detail"), dict):
            return self.data["detail"].get("code", "error")
        return "error"

    @property
    def detail(self) -> dict[str, Any]:
        if isinstance(self.data, dict) and isinstance(self.data.get("detail"), dict):
            return self.data["detail"]
        return {}


def _client_ip() -> str:
    try:
        req = ui.context.client.request
        if req is not None and req.client is not None:
            return req.client.host
    except Exception:  # noqa: BLE001 - no page context (e.g. background task)
        pass
    return "127.0.0.1"


def session() -> dict[str, Any]:
    return app.storage.user


def is_logged_in() -> bool:
    return bool(session().get("access_token"))


def role() -> str | None:
    return session().get("role") if is_logged_in() else None


def store_tokens(tokens: dict[str, Any], *, name: str | None = None) -> None:
    s = session()
    s["access_token"] = tokens["access_token"]
    s["refresh_token"] = tokens["refresh_token"]
    s["role"] = tokens["role"]
    if name is not None:
        s["name"] = name


def clear_session() -> None:
    s = session()
    for k in ("access_token", "refresh_token", "role", "name", "login_id"):
        s.pop(k, None)


async def _send(method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None,
                token: str | None = None, data: dict[str, Any] | None = None,
                files: dict[str, Any] | None = None) -> ApiResult:
    if _api is None:
        raise RuntimeError("api_client not bound")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    transport = httpx.ASGITransport(app=_api, client=(_client_ip(), 0))
    async with httpx.AsyncClient(transport=transport, base_url="http://scheme-finder.internal",
                                 timeout=30) as c:
        r = await c.request(method, path, json=json, data=data, files=files,
                            params={k: v for k, v in (params or {}).items() if v not in (None, "")},
                            headers=headers)
    if not r.content:
        return ApiResult(r.status_code, None)
    if r.headers.get("content-type", "").startswith("application/json"):
        try:
            return ApiResult(r.status_code, r.json())
        except ValueError:
            return ApiResult(r.status_code, None)
    return ApiResult(r.status_code, {"_bytes": r.content, "_type": r.headers.get("content-type", "")})


async def call(method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None,
               auth: bool = True, data: dict[str, Any] | None = None,
               files: dict[str, Any] | None = None) -> ApiResult:
    token = session().get("access_token") if auth else None
    res = await _send(method, path, json=json, params=params, token=token, data=data, files=files)
    if auth and res.status == 401 and session().get("refresh_token"):
        ref = await _send("POST", "/api/auth/refresh", json={"refresh_token": session()["refresh_token"]})
        if ref.ok:
            store_tokens(ref.data)
            res = await _send(method, path, json=json, params=params, token=ref.data["access_token"],
                              data=data, files=files)
        else:
            clear_session()
    return res


async def logout() -> None:
    rt = session().get("refresh_token")
    if rt:
        await _send("POST", "/api/auth/logout", json={"refresh_token": rt})
    clear_session()
