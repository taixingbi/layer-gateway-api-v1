"""Guest chat audit rows in Supabase ``chat_events`` (not ``conversations``)."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request

from app.core.logging import log_event
from app.services.supabase_client import admin_client_configured, get_supabase_admin_client
from app.services.time_util import format_iso_est

ANSWER_PREVIEW_MAX = 500
GUEST_HISTORY_COLUMNS = (
    "id,created_at,auth_guest,user_type,session_id,trace_id,request_id,"
    "conversation_id,prompt,prompt_chars,route,answer_preview,latency_ms,client_ip,user_agent"
)
DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 500


def _client_ip(request: Request) -> str | None:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return None


def _preview(text: str | None, *, max_len: int = ANSWER_PREVIEW_MAX) -> str | None:
    if not text:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def insert_guest_chat_event(
    request: Request,
    *,
    prompt: str,
    answer: str | None = None,
    route: str | None = None,
    latency_ms: dict[str, Any] | None = None,
) -> None:
    """Best-effort audit insert for guest chat; never raises to the caller."""
    auth = getattr(request.state, "auth_context", None) or {}
    if not auth.get("guest"):
        return
    if not admin_client_configured():
        log_event(
            "guest_chat_audit_skipped",
            level="WARN",
            reason="supabase_admin_not_configured",
            path="/v1/chat",
        )
        return

    admin = get_supabase_admin_client()
    if not admin:
        return

    text = prompt.strip()
    if not text:
        return

    conv_id = getattr(request.state, "conversation_id", None)
    row: dict[str, Any] = {
        "auth_guest": True,
        "user_type": "guest",
        "session_id": getattr(request.state, "session_id", None),
        "trace_id": getattr(request.state, "trace_id", None),
        "request_id": getattr(request.state, "request_id", None),
        "conversation_id": str(conv_id) if conv_id else None,
        "prompt": text,
        "prompt_chars": len(text),
        "route": (route or "").strip() or None,
        "answer_preview": _preview(answer),
        "latency_ms": latency_ms or None,
        "client_ip": _client_ip(request),
        "user_agent": (request.headers.get("user-agent") or "").strip() or None,
    }

    try:
        admin.table("chat_events").insert(row).execute()
        log_event(
            "guest_chat_audit_inserted",
            path="/v1/chat",
            trace_id=row.get("trace_id"),
            session_id=row.get("session_id"),
        )
    except Exception as exc:
        log_event(
            "guest_chat_audit_failed",
            level="WARN",
            path="/v1/chat",
            error=str(exc),
            trace_id=row.get("trace_id"),
        )


def _serialize_event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "created_at": format_iso_est(row.get("created_at")),
        "auth_guest": bool(row.get("auth_guest")),
        "user_type": row.get("user_type"),
        "session_id": row.get("session_id"),
        "trace_id": row.get("trace_id"),
        "request_id": row.get("request_id"),
        "conversation_id": row.get("conversation_id"),
        "prompt": row.get("prompt"),
        "prompt_chars": row.get("prompt_chars"),
        "route": row.get("route"),
        "answer_preview": row.get("answer_preview"),
        "latency_ms": row.get("latency_ms"),
        "client_ip": str(row["client_ip"]) if row.get("client_ip") else None,
        "user_agent": row.get("user_agent"),
    }


def list_guest_chat_events(*, limit: int = DEFAULT_LIST_LIMIT) -> list[dict[str, Any]]:
    """List recent guest audit events (admin API; service role)."""
    if not admin_client_configured():
        return []

    admin = get_supabase_admin_client()
    if not admin:
        return []

    cap = min(max(1, limit), MAX_LIST_LIMIT)
    result = (
        admin.table("chat_events")
        .select(GUEST_HISTORY_COLUMNS)
        .eq("auth_guest", True)
        .order("created_at", desc=True)
        .limit(cap)
        .execute()
    )
    return [_serialize_event(row) for row in (result.data or [])]
