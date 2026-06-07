"""Chat backpressure: per-user RPM, per-user concurrency, global chat concurrency."""

from __future__ import annotations

import asyncio
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.metrics import CHAT_STREAMS_INFLIGHT, RATE_LIMIT_REJECTED
from app.services.token_bucket import PerUserRateLimiter

CHAT_PATH = "/v1/chat"
_limiter = PerUserRateLimiter()
_lock = asyncio.Lock()
_chat_inflight = 0
_user_chat_inflight: dict[str, int] = {}


def _user_id(request: Request) -> str | None:
    ctx = getattr(request.state, "auth_context", None)
    if isinstance(ctx, dict):
        uid = (ctx.get("user_id") or "").strip()
        return uid or None
    return None


def _rate_limit_response(
    *,
    status_code: int,
    code: str,
    message: str,
    reason: str,
    retry_after: int | None = None,
    limit: int | None = None,
    remaining: int | None = None,
) -> JSONResponse:
    RATE_LIMIT_REJECTED.labels(reason=reason).inc()
    headers: dict[str, str] = {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    if limit is not None:
        headers["X-RateLimit-Limit"] = str(limit)
    if remaining is not None:
        headers["X-RateLimit-Remaining"] = str(remaining)
    return JSONResponse(
        status_code=status_code,
        content={"status": "error", "error": {"code": code, "message": message}},
        headers=headers,
    )


async def _acquire_chat_slot(user: str | None, settings: Any) -> Response | None:
    """Reserve one chat slot or return a rejection response."""
    global _chat_inflight
    max_global = settings.max_concurrent_chat_streams
    max_user = settings.max_concurrent_streams_per_user

    async with _lock:
        if max_global > 0 and _chat_inflight >= max_global:
            return _rate_limit_response(
                status_code=503,
                code="service_unavailable",
                message="Too many chat streams in progress",
                reason="chat_inflight_global",
            )
        if user and max_user > 0:
            current = _user_chat_inflight.get(user, 0)
            if current >= max_user:
                return _rate_limit_response(
                    status_code=429,
                    code="too_many_requests",
                    message="Too many concurrent chat streams for this user",
                    reason="chat_inflight_user",
                    retry_after=5,
                )
        _chat_inflight += 1
        CHAT_STREAMS_INFLIGHT.set(_chat_inflight)
        if user:
            _user_chat_inflight[user] = _user_chat_inflight.get(user, 0) + 1
    return None


async def _release_chat_slot(user: str | None) -> None:
    global _chat_inflight
    async with _lock:
        _chat_inflight = max(0, _chat_inflight - 1)
        CHAT_STREAMS_INFLIGHT.set(_chat_inflight)
        if user and user in _user_chat_inflight:
            next_val = _user_chat_inflight[user] - 1
            if next_val <= 0:
                _user_chat_inflight.pop(user, None)
            else:
                _user_chat_inflight[user] = next_val


class ChatLimitsMiddleware(BaseHTTPMiddleware):
    """Rate-limit and concurrency-cap ``POST /v1/chat`` after auth."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method != "POST" or request.url.path != CHAT_PATH:
            return await call_next(request)

        settings = get_settings()
        user = _user_id(request)

        rpm = settings.rate_limit_chat_requests_per_min
        if rpm > 0 and user:
            async with _lock:
                allowed, bucket = _limiter.check(
                    user,
                    rate_per_minute=float(rpm),
                    burst=settings.rate_limit_chat_burst,
                )
            if not allowed:
                return _rate_limit_response(
                    status_code=429,
                    code="too_many_requests",
                    message="Chat rate limit exceeded",
                    reason="chat_rpm",
                    retry_after=bucket.retry_after_seconds(),
                    limit=bucket.limit,
                    remaining=bucket.remaining,
                )

        reject = await _acquire_chat_slot(user, settings)
        if reject is not None:
            return reject

        try:
            return await call_next(request)
        finally:
            await _release_chat_slot(user)


def reset_chat_limits_for_tests() -> None:
    """Clear in-process counters (unit tests only)."""
    global _chat_inflight
    _chat_inflight = 0
    _user_chat_inflight.clear()
    _limiter._buckets.clear()
    CHAT_STREAMS_INFLIGHT.set(0)
