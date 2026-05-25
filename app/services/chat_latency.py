"""Chat response ``latency_ms`` envelope (gateway phases + orchestrator upstream)."""

from __future__ import annotations

import time
from typing import Any

from starlette.requests import Request


def _round_ms(value: float) -> int:
    return int(round(value))


def orchestrator_workflow_from_source(source: Any) -> dict[str, Any] | None:
    """Extract orchestrator workflow timings (flat upstream JSON, not the gateway envelope)."""
    if source is None:
        return None
    if isinstance(source, dict):
        if "workflow" in source and isinstance(source["workflow"], dict):
            return source["workflow"]
        nested = source.get("latency_ms")
        if isinstance(nested, dict):
            orch = nested.get("orchestrator")
            if isinstance(orch, dict) and isinstance(orch.get("workflow"), dict):
                return orch["workflow"]
            if "storage" in nested or "auth" in nested:
                return None
            if nested:
                return nested
        timings = source.get("latency_ms") or source.get("timings_ms") or source.get("timings")
        if isinstance(timings, dict) and timings.get("orchestrator"):
            orch = timings["orchestrator"]
            if isinstance(orch, dict) and isinstance(orch.get("workflow"), dict):
                return orch["workflow"]
        if isinstance(timings, dict) and timings and "storage" not in timings and "auth" not in timings:
            return timings
        return None
    if hasattr(source, "model_dump"):
        return orchestrator_workflow_from_source(source.model_dump(mode="json"))
    return orchestrator_workflow_from_source(
        {
            "latency_ms": getattr(source, "latency_ms", None),
            "timings_ms": getattr(source, "timings_ms", None),
        }
    )


def orchestrator_latency_ms(source: Any) -> dict[str, Any] | None:
    """Backward-compatible alias: returns workflow dict or legacy flat orchestrator timings."""
    return orchestrator_workflow_from_source(source)


def build_orchestrator_section(
    workflow: dict[str, Any] | None,
    *,
    proxy_total_ms: float,
) -> dict[str, Any] | None:
    """Wrap upstream workflow timings with gateway-measured ``proxy_total``."""
    if not workflow and proxy_total_ms <= 0:
        return None
    if isinstance(workflow, dict) and "workflow" in workflow and "proxy_total" in workflow:
        section = dict(workflow)
        section["proxy_total"] = _round_ms(proxy_total_ms or section.get("proxy_total", 0))
        return section
    return {
        "proxy_total": _round_ms(proxy_total_ms),
        "workflow": dict(workflow) if isinstance(workflow, dict) else {},
    }


def build_chat_latency_ms(
    *,
    auth_ms: float | None = None,
    request_validation_ms: float = 0.0,
    db_write_user_message_ms: float = 0.0,
    orchestrator_call_ms: float = 0.0,
    db_write_assistant_message_ms: float = 0.0,
    orchestrator_workflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build ``latency_ms`` for gateway chat JSON and SSE ``done`` events."""
    auth = _round_ms(auth_ms or 0)
    validation = _round_ms(request_validation_ms)
    write_user = _round_ms(db_write_user_message_ms)
    write_assistant = _round_ms(db_write_assistant_message_ms)
    storage: dict[str, Any] = {
        "total": write_user + write_assistant,
        "write_user_message": write_user,
        "write_assistant_message": write_assistant,
    }
    orch_section = build_orchestrator_section(
        orchestrator_workflow,
        proxy_total_ms=orchestrator_call_ms,
    )
    proxy_total = (
        orch_section["proxy_total"]
        if orch_section
        else _round_ms(orchestrator_call_ms)
    )
    out: dict[str, Any] = {
        "total": auth + validation + storage["total"] + proxy_total,
        "auth": auth,
        "validation": validation,
        "storage": storage,
    }
    if orch_section:
        out["orchestrator"] = orch_section
    return out


def auth_latency_ms(request: Request) -> float | None:
    """Auth duration recorded by ``AuthMiddleware`` (milliseconds)."""
    value = getattr(request.state, "auth_ms", None)
    return float(value) if isinstance(value, (int, float)) else None


class ChatLatencyRecorder:
    """Mutable per-request phase timings for ``POST /v1/chat``."""

    def __init__(self) -> None:
        self.request_validation_ms = 0.0
        self.db_write_user_message_ms = 0.0
        self.orchestrator_call_ms = 0.0
        self.db_write_assistant_message_ms = 0.0

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
        """Legacy hook; response assembly is included in orchestrator proxy time."""

    def build(
        self,
        request: Request,
        *,
        orchestrator_workflow: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return build_chat_latency_ms(
            auth_ms=auth_latency_ms(request),
            request_validation_ms=self.request_validation_ms,
            db_write_user_message_ms=self.db_write_user_message_ms,
            orchestrator_call_ms=self.orchestrator_call_ms,
            db_write_assistant_message_ms=self.db_write_assistant_message_ms,
            orchestrator_workflow=orchestrator_workflow,
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
    orchestrator_workflow: dict[str, Any] | None,
) -> None:
    """Set ``latency_ms`` on a response/done dict; drop legacy timing keys."""
    payload.pop("timings_ms", None)
    raw = payload.get("latency_ms")
    if orchestrator_workflow is None and isinstance(raw, dict):
        if "orchestrator" in raw and isinstance(raw["orchestrator"], dict):
            orchestrator_workflow = raw["orchestrator"].get("workflow")
        elif "storage" not in raw and "auth" not in raw:
            orchestrator_workflow = raw
    if isinstance(raw, dict):
        payload.pop("latency_ms", None)
    recorder = chat_latency_recorder(request)
    payload["latency_ms"] = recorder.build(request, orchestrator_workflow=orchestrator_workflow)
