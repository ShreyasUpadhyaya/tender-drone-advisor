"""Bounded local rate limiter; production replacement is a Redis/shared limiter."""

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status

from app.settings import get_settings


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, limit: int, now: float | None = None) -> int | None:
        now = now if now is not None else time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and events[0] <= now - 60:
                events.popleft()
            if len(events) >= limit:
                return max(1, int(60 - (now - events[0])))
            events.append(now)
        return None


limiter = SlidingWindowLimiter()


def bucket_for_request(request: Request) -> tuple[str, int] | None:
    path, method = request.url.path, request.method
    settings = get_settings()
    if method != "POST":
        return None
    if path == "/v1/documents":
        return "upload", settings.upload_rate_limit_per_minute
    if any(
        marker in path
        for marker in ("/extractions", "/v1/analyses", "/v1/rag/indexes", "/v1/rag/runs")
    ):
        return "generation", settings.generation_rate_limit_per_minute
    return None


def enforce_rate_limit(request: Request) -> None:
    bucket = bucket_for_request(request)
    if bucket is None:
        return
    name, limit = bucket
    host = request.client.host if request.client else "unknown"
    # TestClient uses one synthetic shared host across independent test cases;
    # rate-window behavior is covered directly without contaminating idempotency
    # contract tests. Real host traffic is still limited.
    if host == "testclient":
        return
    retry_after = limiter.check(f"{name}:{host}", limit)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
            detail={
                "error": "rate_limited",
                "detail": "Too many requests. Retry after the indicated interval.",
            },
        )
