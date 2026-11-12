"""Domain error type. Routers turn it into an HTTP response with a stable ``code`` that the UI
translates into Hindi or English."""

from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    def __init__(self, code: str, status: int = 400, **extra: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.extra = extra
