"""Bounded in-flight request concurrency with 503 when at capacity."""

import asyncio

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.metrics import INFLIGHT, REJECTED_TOTAL
from app.middleware.paths import PUBLIC_PROBE_PATHS


def _exempt_path(path: str) -> bool:
    """Return True for probes, OpenAPI, and docs (no inflight slot)."""
    return path in PUBLIC_PROBE_PATHS or path.startswith("/docs") or path == "/openapi.json"


class InflightLimitMiddleware(BaseHTTPMiddleware):
    """Bounded concurrency: return 503 when too many requests are in flight."""

    def __init__(self, app):
        """Initialize inflight counter and lock."""
        super().__init__(app)
        self._lock = asyncio.Lock()
        self._inflight = 0

    async def dispatch(self, request: Request, call_next) -> Response:
        """Increment inflight count or reject with 503 when over limit."""
        if _exempt_path(request.url.path):
            return await call_next(request)

        settings = get_settings()
        max_n = settings.max_inflight_requests
        if max_n <= 0:
            return await call_next(request)

        async with self._lock:
            if self._inflight >= max_n:
                REJECTED_TOTAL.labels(reason="inflight_limit").inc()
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "error",
                        "error": {"code": "service_unavailable", "message": "gateway at capacity"},
                    },
                )
            self._inflight += 1
            INFLIGHT.set(self._inflight)

        try:
            return await call_next(request)
        finally:
            async with self._lock:
                self._inflight -= 1
                INFLIGHT.set(self._inflight)
