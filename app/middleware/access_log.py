"""Structured access logging and Prometheus observation for every request."""

import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from app.core.config import get_settings
from app.core.logging import log_event
from app.core.metrics import LATENCY_MS, REQUESTS_TOTAL, TTFB_MS


def _path_label(path: str) -> str:
    """Bound cardinality for Prometheus: collapse dynamic segments if added later."""
    return path


def _omit_none_values(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop null optional fields so JSON logs stay compact."""
    return {k: v for k, v in fields.items() if v is not None}


def _emit_request_complete(
    *,
    request: Request,
    status_code: int,
    latency_ms: float,
    ttfb_ms: float | None,
    stream: bool,
) -> None:
    """Emit ``request_complete`` log line and update Prometheus histograms."""
    settings = get_settings()
    session_id = getattr(request.state, "session_id", None)
    conversation_id = getattr(request.state, "conversation_id", None)
    fields = {
        "service": settings.service_name,
        # Effective correlation IDs (from X-Request-Id / X-Trace-Id when present, else minted).
        "request_id": getattr(request.state, "request_id", None),
        "trace_id": getattr(request.state, "trace_id", None),
        "session_id": session_id,
        "conversation_id": conversation_id,
        "path": request.url.path,
        "method": request.method,
        "status": status_code,
        "latency_ms": round(latency_ms, 3),
        "stream": stream,
        "backend": "orchestrator"
        if request.url.path == "/api/chat" and request.method == "POST"
        else "-",
    }
    if ttfb_ms is not None:
        fields["ttfb_ms"] = round(ttfb_ms, 3)
    if status_code >= 400:
        error_detail = getattr(request.state, "error_detail", None)
        if error_detail:
            fields["error_detail"] = error_detail

    log_event("request_complete", **_omit_none_values(fields))

    path_l = _path_label(request.url.path)
    REQUESTS_TOTAL.labels(method=request.method, path=path_l, status=str(status_code)).inc()
    LATENCY_MS.labels(method=request.method, path=path_l).observe(latency_ms)
    if ttfb_ms is not None:
        TTFB_MS.labels(method=request.method, path=path_l).observe(ttfb_ms)


class StructuredAccessLogMiddleware(BaseHTTPMiddleware):
    """Log ``request_complete``; for SSE, measure TTFB and total stream time."""

    async def dispatch(self, request: Request, call_next) -> Response:
        """Measure latency (and TTFB for SSE) after the response completes."""
        start = time.perf_counter()
        response = await call_next(request)

        if isinstance(response, StreamingResponse):
            body = response.body_iterator

            async def logging_wrapper():
                """Stream body while recording TTFB and total latency on close."""
                try:
                    async for chunk in body:
                        if await request.is_disconnected():
                            break
                        yield chunk
                finally:
                    latency_ms = (time.perf_counter() - start) * 1000
                    ttfb = getattr(request.state, "stream_ttfb_ms", None)
                    stream = getattr(request.state, "access_log_stream", True)
                    _emit_request_complete(
                        request=request,
                        status_code=response.status_code,
                        latency_ms=latency_ms,
                        ttfb_ms=ttfb,
                        stream=stream,
                    )

            return StreamingResponse(
                logging_wrapper(),
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
                background=getattr(response, "background", None),
            )

        latency_ms = (time.perf_counter() - start) * 1000
        stream = getattr(request.state, "access_log_stream", False)
        _emit_request_complete(
            request=request,
            status_code=response.status_code,
            latency_ms=latency_ms,
            ttfb_ms=None,
            stream=stream,
        )
        return response
