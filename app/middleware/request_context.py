from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.middleware.paths import PUBLIC_PROBE_PATHS


def _with_prefix(prefix: str) -> str:
    """Generate short correlation IDs with a fixed semantic prefix."""
    return f"{prefix}_{uuid4().hex[:12]}"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Probes and metrics: do not mint, persist, or echo request/trace IDs (keep scrapers/probes noise-free).
        if request.url.path in PUBLIC_PROBE_PATHS:
            return await call_next(request)

        # Reuse inbound IDs when present; otherwise mint new correlation IDs.
        request_id = request.headers.get("x-request-id") or _with_prefix("req")
        trace_id = request.headers.get("x-trace-id") or _with_prefix("trace")

        # Persist IDs in request state so handlers and services can log them.
        request.state.request_id = request_id
        request.state.trace_id = trace_id

        # Continue request processing through downstream middleware/route.
        response = await call_next(request)

        # Echo IDs back to callers to support end-to-end troubleshooting.
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Trace-Id"] = trace_id
        return response
