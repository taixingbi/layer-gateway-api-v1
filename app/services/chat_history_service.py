"""Supabase conversations and messages persistence for chat history."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.schemas.history import ChatHistoryMessage
from app.services.supabase_client import get_supabase_admin_client, get_supabase_client, require_supabase
from app.services.time_util import format_iso_est

CONVERSATION_COLUMNS = "id,user_id,title,created_at,updated_at"
MESSAGE_STATUS_COMPLETE = "complete"


def _message_columns() -> str:
    """PostgREST select list; ``status`` only when the column exists in Supabase."""
    cols = ["id", "conversation_id", "role", "content"]
    if get_settings().chat_persist_message_status:
        cols.append("status")
    cols.extend(["metadata", "created_at"])
    return ",".join(cols)


def message_persist_status() -> str | None:
    """Status value for inserts, or None when the DB has no ``status`` column."""
    return MESSAGE_STATUS_COMPLETE if get_settings().chat_persist_message_status else None
TITLE_MAX_LEN = 80
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100


class ChatHistoryUnavailable(Exception):
    """Supabase not configured; caller may skip persistence."""


def persistence_enabled() -> bool:
    """True when anon Supabase client can be used for chat history."""
    return get_supabase_client() is not None


def _validate_uuid(value: str, field_name: str = "conversation_id") -> str:
    """Parse and normalize a UUID string or raise 400."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be a valid UUID",
        ) from exc


def _table(access_token: str, name: str):
    """Return PostgREST table handle (admin or user-scoped)."""
    admin = get_supabase_admin_client()
    if admin:
        return admin.table(name)
    supabase = require_supabase()
    supabase.postgrest.auth(access_token)
    return supabase.table(name)


def _handle_db_error(exc: Exception) -> None:
    """Map PostgREST/RLS errors to HTTP exceptions."""
    if isinstance(exc, APIError):
        message = exc.message or str(exc)
        if "schema cache" in message.lower() or "could not find" in message.lower():
            raise HTTPException(
                status_code=400,
                detail=f"chat history column mismatch: {message}",
            ) from exc
        if exc.code == "42501" or "row-level security" in message.lower():
            raise HTTPException(
                status_code=403,
                detail=(
                    "Chat history blocked by Supabase RLS. Add SUPABASE_SERVICE_KEY "
                    "to gateway .env, or run sql/chat_history_rls.sql in Supabase."
                ),
            ) from exc
        raise HTTPException(status_code=400, detail=message) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


def merge_history(
    db_history: list[ChatHistoryMessage],
    client_history: list[ChatHistoryMessage],
) -> list[ChatHistoryMessage]:
    """Prefer DB history; append unsaved client tail turns."""
    if not client_history:
        return list(db_history)
    if len(client_history) <= len(db_history):
        return list(db_history)
    return list(db_history) + list(client_history[len(db_history) :])


def _conversation_title(message: str) -> str:
    """Derive conversation title from first user message."""
    text = message.strip()
    if len(text) <= TITLE_MAX_LEN:
        return text
    return text[:TITLE_MAX_LEN].rstrip() + "…"


def _row_to_conversation_summary(row: dict) -> dict:
    """Serialize conversation list item."""
    return {
        "id": row["id"],
        "title": row.get("title"),
        "created_at": format_iso_est(row.get("created_at")),
        "updated_at": format_iso_est(row.get("updated_at")),
    }


def assistant_message_metadata(
    *,
    rewrite: str | None = None,
    citations: list[dict[str, Any]] | None = None,
    follow_up_questions: list[str] | None = None,
    model: str | None = None,
) -> dict[str, Any] | None:
    """Build optional ``metadata`` jsonb for assistant rows (omit when empty)."""
    meta: dict[str, Any] = {}
    if rewrite and rewrite.strip():
        meta["rewrite"] = rewrite.strip()
    if citations:
        meta["citations"] = citations
    if follow_up_questions:
        meta["follow_up_questions"] = follow_up_questions
    if model and model.strip():
        meta["model"] = model.strip()
    return meta or None


def _model_from_usage(usage: dict[str, Any] | None) -> str | None:
    """Extract model name from orchestrator usage payload when present."""
    if not usage:
        return None
    for key in ("model", "model_name"):
        value = usage.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _row_to_message(row: dict) -> dict:
    """Serialize one message for API response."""
    out: dict[str, Any] = {
        "id": row.get("id"),
        "role": row["role"],
        "content": row["content"],
        "created_at": format_iso_est(row.get("created_at")),
    }
    status = row.get("status")
    if isinstance(status, str) and status.strip():
        out["status"] = status.strip()
    metadata = row.get("metadata")
    if isinstance(metadata, dict) and metadata:
        out["metadata"] = metadata
    return out


def ensure_conversation(
    access_token: str,
    user_id: str,
    conversation_id: str | None,
    *,
    first_message: str,
) -> str:
    """Return existing or newly created conversation id owned by user_id."""
    if not persistence_enabled():
        raise ChatHistoryUnavailable()

    if conversation_id is None:
        new_id = str(uuid.uuid4())
        insert = {
            "id": new_id,
            "user_id": user_id,
            "title": _conversation_title(first_message),
        }
        try:
            result = _table(access_token, "conversations").insert(insert).execute()
        except Exception as exc:
            _handle_db_error(exc)
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create conversation")
        return new_id

    cid = _validate_uuid(conversation_id)
    try:
        result = (
            _table(access_token, "conversations")
            .select(CONVERSATION_COLUMNS)
            .eq("id", cid)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        _handle_db_error(exc)
    if not result.data:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return cid


def load_messages(
    access_token: str,
    user_id: str,
    conversation_id: str,
) -> list[ChatHistoryMessage]:
    """Load prior turns for orchestrator history (excludes current request message)."""
    if not persistence_enabled():
        raise ChatHistoryUnavailable()

    cid = _validate_uuid(conversation_id)
    _assert_conversation_owned(access_token, user_id, cid)
    try:
        result = (
            _table(access_token, "messages")
            .select(_message_columns())
            .eq("conversation_id", cid)
            .order("created_at", desc=False)
            .execute()
        )
    except Exception as exc:
        _handle_db_error(exc)

    out: list[ChatHistoryMessage] = []
    for row in result.data or []:
        role = row.get("role")
        content = (row.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        out.append(ChatHistoryMessage(role=role, content=content))
    return out


def append_message(
    access_token: str,
    user_id: str,
    conversation_id: str,
    role: str,
    content: str,
    *,
    status: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Insert one message row; return new message id when Supabase returns it."""
    if not persistence_enabled():
        raise ChatHistoryUnavailable()

    if role not in ("user", "assistant"):
        raise HTTPException(status_code=400, detail="message role must be user or assistant")
    text = content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="message content cannot be empty")

    cid = _validate_uuid(conversation_id)
    _assert_conversation_owned(access_token, user_id, cid)
    insert: dict[str, Any] = {"conversation_id": cid, "role": role, "content": text}
    if status is not None:
        insert["status"] = status
    if metadata is not None:
        insert["metadata"] = metadata
    try:
        result = _table(access_token, "messages").insert(insert).select("id").execute()
    except Exception as exc:
        _handle_db_error(exc)
    rows = result.data or []
    if rows and rows[0].get("id"):
        return str(rows[0]["id"])
    return None


def list_conversations(
    access_token: str,
    user_id: str,
    *,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[dict[str, Any]]:
    """List conversations for user, newest updated_at first."""
    if not persistence_enabled():
        raise ChatHistoryUnavailable()

    cap = min(max(1, limit), MAX_LIST_LIMIT)
    try:
        result = (
            _table(access_token, "conversations")
            .select(CONVERSATION_COLUMNS)
            .eq("user_id", user_id)
            .order("updated_at", desc=True)
            .limit(cap)
            .execute()
        )
    except Exception as exc:
        _handle_db_error(exc)
    return [_row_to_conversation_summary(row) for row in (result.data or [])]


def list_messages_for_api(
    access_token: str,
    user_id: str,
    conversation_id: str,
) -> list[dict[str, Any]]:
    """Return serialized messages for GET /conversations/{id}/messages."""
    if not persistence_enabled():
        raise ChatHistoryUnavailable()

    cid = _validate_uuid(conversation_id)
    _assert_conversation_owned(access_token, user_id, cid)
    try:
        result = (
            _table(access_token, "messages")
            .select(_message_columns())
            .eq("conversation_id", cid)
            .order("created_at", desc=False)
            .execute()
        )
    except Exception as exc:
        _handle_db_error(exc)
    return [_row_to_message(row) for row in (result.data or [])]


def _assert_conversation_owned(access_token: str, user_id: str, conversation_id: str) -> None:
    """Raise 404 when conversation is missing or not owned by user_id."""
    try:
        result = (
            _table(access_token, "conversations")
            .select("id")
            .eq("id", conversation_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        _handle_db_error(exc)
    if not result.data:
        raise HTTPException(status_code=404, detail="Conversation not found")
