"""Normalize orchestrator ``/v1/orchestrator/answer`` envelopes for gateway parsing."""

from __future__ import annotations

from typing import Any


def route_label_from_route_obj(route: Any) -> str | None:
    """Derive a flat route label from ``meta.route`` or ``route_detail``."""
    if not isinstance(route, dict):
        return None
    tool = route.get("tool")
    if isinstance(tool, str) and tool.strip():
        return tool.strip()
    intent = route.get("intent")
    if isinstance(intent, str) and intent.strip():
        return intent.strip()
    name = route.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def normalize_orchestrator_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested ``answer`` / ``meta`` fields into gateway/orchestrator schema shape."""
    if not isinstance(parsed, dict):
        return {}
    out = dict(parsed)

    answer = out.get("answer")
    if isinstance(answer, dict):
        text = answer.get("text")
        if isinstance(text, str):
            out["answer"] = text
        if not out.get("citations") and isinstance(answer.get("citations"), list):
            out["citations"] = answer["citations"]

    meta = out.get("meta")
    if isinstance(meta, dict):
        rewrite = meta.get("rewrite")
        if isinstance(rewrite, str) and rewrite.strip() and not out.get("rewrite"):
            out["rewrite"] = rewrite.strip()
        route_obj = meta.get("route")
        if isinstance(route_obj, dict):
            label = route_label_from_route_obj(route_obj)
            if label:
                out["route"] = label
            out["route_meta"] = route_obj
        tool = meta.get("tool")
        if isinstance(tool, dict):
            out["tool_meta"] = tool

    out.pop("type", None)
    out.pop("stream", None)
    return out


def gateway_done_fields_from_normalized(normalized: dict[str, Any]) -> dict[str, Any]:
    """Build optional gateway ``done`` SSE fields (route, citations, usage, …)."""
    body: dict[str, Any] = {}
    status = normalized.get("status")
    if isinstance(status, dict) and status.get("ok"):
        body["status"] = "success"
    elif isinstance(status, str):
        body["status"] = status
    else:
        body["status"] = "success"

    rewrite = normalized.get("rewrite")
    if isinstance(rewrite, str) and rewrite.strip():
        body["rewrite"] = rewrite.strip()

    citations = normalized.get("citations")
    if isinstance(citations, list):
        body["citations"] = [c for c in citations if isinstance(c, dict)]

    follow = normalized.get("follow_up_questions")
    if isinstance(follow, list):
        body["follow_up_questions"] = [
            str(q).strip() for q in follow if isinstance(q, str) and str(q).strip()
        ]

    route = normalized.get("route")
    if isinstance(route, str) and route.strip():
        body["route"] = route.strip()

    route_meta = normalized.get("route_meta")
    if isinstance(route_meta, dict):
        body["route_detail"] = {
            "type": route_meta.get("type"),
            "name": route_meta.get("tool") or route_meta.get("intent"),
            "confidence": route_meta.get("confidence"),
            "reason": route_meta.get("reason"),
        }
        source = route_meta.get("source")
        if isinstance(source, str):
            body["route_source"] = source

    route_detail = normalized.get("route_detail")
    if isinstance(route_detail, dict):
        body["route_detail"] = route_detail
    route_source = normalized.get("route_source")
    if isinstance(route_source, str):
        body["route_source"] = route_source

    latency = normalized.get("latency_ms") or normalized.get("timings_ms")
    if isinstance(latency, dict) and latency:
        body["latency_ms"] = latency

    usage = normalized.get("usage")
    if isinstance(usage, dict) and usage:
        body["usage"] = usage

    route_meta = normalized.get("route_meta")
    if isinstance(route_meta, dict):
        body["route_meta"] = route_meta
    tool_meta = normalized.get("tool_meta")
    if isinstance(tool_meta, dict):
        body["tool_meta"] = tool_meta

    return body
