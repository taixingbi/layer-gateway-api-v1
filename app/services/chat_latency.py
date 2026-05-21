"""Chat response ``latency_ms`` envelope (gateway phases + orchestrator upstream)."""

from __future__ import annotations

import time
from typing import Any

from starlette.requests import Request


def orchestrator_latency_ms(source: Any) -> dict[str, Any] | None:
    """Extract orchestrator latency object from JSON, Pydantic model, or SSE ``done`` payload."""
    if source is None:
        return None
    if isinstance(source, dict):
        nested = source.get("latency_ms")
        if isinstance(nested, dict) and nested.get("orchestrator"):
            nested = nested["orchestrator"]
        if isinstance(nested, dict) and nested:
            return nested
        timings = source.get("latency_ms") or source.get("timings_ms") or source.get("timings")
        if isinstance(timings, dict) and timings.get("orchestrator"):
            return timings["orchestrator"]
        return timings if isinstance(timings, dict) and timings else None
    if hasattr(source, "model_dump"):
        data = source.model_dump(mode="json")
        return orchestrator_latency_ms(data)
    return orchestrator_latency_ms(
        {
            "latency_ms": getattr(source, "latency_ms", None),
            "timings_ms": getattr(source, "timings_ms", None),
        }
    )


def _round_gateway_ms(value: float) -> int:
    return int(round(value))


def build_chat_latency_ms(
    *,
    auth_ms: float | None = None,
    request_validation_ms: float = 0.0,
    db_write_user_message_ms: float = 0.0,
    orchestrator_call_ms: float = 0.0,
    db_write_assistant_message_ms: float = 0.0,
    response_stream_ms: float = 0.0,
    orchestrator: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build ``latency_ms`` for gateway chat JSON and SSE ``done`` events."""
    gateway_api: dict[str, Any] = {
        "auth": _round_gateway_ms(auth_ms or 0),
        "request_validation": _round_gateway_ms(request_validation_ms),
        "db_write_user_message": _round_gateway_ms(db_write_user_message_ms),
        "orchestrator_call": _round_gateway_ms(orchestrator_call_ms),
        "db_write_assistant_message": _round_gateway_ms(db_write_assistant_message_ms),
        "response_stream": _round_gateway_ms(response_stream_ms),
    }
    gateway_api["total"] = sum(
        gateway_api[k]
        for k in (
            "auth",
            "request_validation",
            "db_write_user_message",
            "orchestrator_call",
            "db_write_assistant_message",
            "response_stream",
        )
    )
    out: dict[str, Any] = {"gateway_api": gateway_api}
    if orchestrator:
        out["orchestrator"] = orchestrator
    return out


def auth_latency_ms(request: Request) -> float | None:
    """Auth duration recorded by ``AuthMiddleware`` (milliseconds)."""
    value = getattr(request.state, "auth_ms", None)
    return float(value) if isinstance(value, (int, float)) else None


class ChatLatencyRecorder:
    """Mutable per-request phase timings for ``POST /api/chat``."""

    def __init__(self) -> None:
        self.request_validation_ms = 0.0
        self.db_write_user_message_ms = 0.0
        self.orchestrator_call_ms = 0.0
        self.db_write_assistant_message_ms = 0.0
        self.response_stream_ms = 0.0

    def measure(self) -> float:
        """Return a perf_counter snapshot."""
        return time.perf_counter()

    def add_request_validation(self, start: float) -> None:
        self.request_validation_ms += (time.perf_counter() - start) * 1000

    def add_db_write_user_message(self, start: float) -> None:
        self.db_write_user_message_ms += (time.perf_counter() - start) * 1000

    def add_orchestrator_call(self, start: float) -> None:
        self.orchestrator_call_ms += (time.perf_counter() - start) * 1000

    def add_db_write_assistant_message(self, start: float) -> None:
        self.db_write_assistant_message_ms += (time.perf_counter() - start) * 1000

    def add_response_stream(self, start: float) -> None:
        self.response_stream_ms += (time.perf_counter() - start) * 1000

    def build(self, request: Request, *, orchestrator: dict[str, Any] | None) -> dict[str, Any]:
        return build_chat_latency_ms(
            auth_ms=auth_latency_ms(request),
            request_validation_ms=self.request_validation_ms,
            db_write_user_message_ms=self.db_write_user_message_ms,
            orchestrator_call_ms=self.orchestrator_call_ms,
            db_write_assistant_message_ms=self.db_write_assistant_message_ms,
            response_stream_ms=self.response_stream_ms,
            orchestrator=orchestrator,
        )


def chat_latency_recorder(request: Request) -> ChatLatencyRecorder:
    """Get or create the chat latency recorder on ``request.state``."""
    recorder = getattr(request.state, "chat_latency", None)
    if recorder is None:
        recorder = ChatLatencyRecorder()
        request.state.chat_latency = recorder
    return recorder


def attach_latency_to_payload(
    payload: dict[str, Any],
    request: Request,
    *,
    orchestrator: dict[str, Any] | None,
) -> None:
    """Set nested ``latency_ms`` on a response/done dict; drop legacy flat timing keys."""
    payload.pop("timings_ms", None)
    flat = payload.get("latency_ms")
    if isinstance(flat, dict) and "gateway_api" not in flat and "orchestrator" not in flat:
        orchestrator = orchestrator or flat
        payload.pop("latency_ms", None)
    recorder = chat_latency_recorder(request)
    payload["latency_ms"] = recorder.build(request, orchestrator=orchestrator)
