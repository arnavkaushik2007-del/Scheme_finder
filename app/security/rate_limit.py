"""In-process sliding-window rate limiter.

Good enough for a single-instance prototype. A multi-instance deployment would move the
counters to Redis or MongoDB (listed in the README under "Known limitations").
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str, limit: int, window_s: float = 60.0) -> tuple[bool, int]:
        """Record one hit. Returns (allowed, seconds_until_retry)."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False, max(1, int(window_s - (now - q[0])) + 1)
            q.append(now)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce(request: Request, bucket: str, limit: int) -> None:
    allowed, retry = limiter.hit(f"{bucket}:{client_ip(request)}", limit)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "retry_after": retry},
            headers={"Retry-After": str(retry)},
        )
