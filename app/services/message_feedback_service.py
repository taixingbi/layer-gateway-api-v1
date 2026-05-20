"""Supabase ``message_feedback`` persistence for chat message ratings and annotations."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException

from app.core.config import get_settings
from app.services.chat_history_service import (
    ChatHistoryUnavailable,
    _assert_conversation_owned,
    _handle_db_error,
    _table,
    _validate_uuid,
    default_chat_route_label,
    persistence_enabled,
)
from app.services.time_util import format_iso_est

FEEDBACK_SELECT_COLUMNS = (
    "id,message_id,conversation_id,user_id,feedback,feedback_reason,preference_score,"
    "reviewer_type,model,prompt_version,route,feedback_comment,labeler_notes,metadata,created_at"
)

REVIEWER_END_USER = "end_user"

# Matches Supabase ``message_feedback`` reason check (orchestrator enum).
DB_FEEDBACK_REASONS = frozenset(
    {
        "biased",
        "incomplete_instructions",
        "not_factual",
        "not_relevant",
        "other",
        "style_tone",
        "unsafe",
    }
)
UI_THUMBS_RATINGS = frozenset({"thumbs_up", "thumbs_down"})


def _prepare_feedback_reason_for_db(
    feedback_reason: str | None,
    metadata: dict[str, Any] | None,
) -> tuple[str | None, dict[str, Any]]:
    """Normalize reason; thumbs ratings live in ``metadata.rating`` only."""
    meta = dict(metadata or {})
    raw = (feedback_reason or "").strip()
    if not raw:
        return None, meta
    if raw in UI_THUMBS_RATINGS:
        meta.setdefault("rating", raw)
        return None, meta
    if raw in DB_FEEDBACK_REASONS:
        return raw, meta
    meta.setdefault("raw_feedback_reason", raw)
    return "other", meta


def feedback_persistence_enabled() -> bool:
    """True when Supabase client can persist feedback rows."""
    return persistence_enabled()


def _row_to_feedback(row: dict[str, Any]) -> dict[str, Any]:
    """Serialize one feedback row for API response."""
    out: dict[str, Any] = {
        "id": row.get("id"),
        "message_id": row.get("message_id"),
        "conversation_id": row.get("conversation_id"),
        "created_at": format_iso_est(row.get("created_at")),
    }
    if row.get("user_id"):
        out["user_id"] = row["user_id"]
    for key in (
        "feedback",
        "feedback_reason",
        "preference_score",
        "reviewer_type",
        "model",
        "prompt_version",
        "route",
        "feedback_comment",
        "labeler_notes",
    ):
        val = row.get(key)
        if val is not None:
            out[key] = val
    metadata = row.get("metadata")
    if isinstance(metadata, dict) and metadata:
        out["metadata"] = metadata
        if metadata.get("rating"):
            out["rating"] = metadata["rating"]
    return out


def _model_route_from_message(
    access_token: str,
    message_id: str,
    conversation_id: str,
) -> tuple[str | None, str | None]:
    """Read ``model`` / ``route`` from the assistant message ``metadata`` jsonb."""
    try:
        result = (
            _table(access_token, "messages")
            .select("metadata")
            .eq("id", message_id)
            .eq("conversation_id", conversation_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        _handle_db_error(exc)
    if not result.data:
        return None, None
    meta = result.data[0].get("metadata")
    if not isinstance(meta, dict):
        return None, None
    model = meta.get("model")
    route = meta.get("route")
    return (
        model.strip() if isinstance(model, str) and model.strip() else None,
        route.strip() if isinstance(route, str) and route.strip() else None,
    )


def _assert_message_in_conversation(
    access_token: str,
    user_id: str,
    message_id: str,
    conversation_id: str,
) -> tuple[str, str]:
    """Ensure message exists in an owned conversation; return normalized UUIDs."""
    cid = _validate_uuid(conversation_id, "conversation_id")
    mid = _validate_uuid(message_id, "message_id")
    _assert_conversation_owned(access_token, user_id, cid)
    try:
        result = (
            _table(access_token, "messages")
            .select("id")
            .eq("id", mid)
            .eq("conversation_id", cid)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        _handle_db_error(exc)
    if not result.data:
        raise HTTPException(status_code=404, detail="Message not found")
    return mid, cid


def _resolve_model_route_for_feedback(
    access_token: str,
    message_id: str,
    conversation_id: str,
    *,
    model: str | None,
    route: str | None,
) -> tuple[str | None, str | None]:
    """Prefer explicit body fields; else assistant message metadata; else gateway defaults."""
    msg_model, msg_route = _model_route_from_message(access_token, message_id, conversation_id)
    resolved_model = (model or "").strip() or msg_model
    if not resolved_model:
        resolved_model = (get_settings().chat_assistant_model or "").strip() or None
    resolved_route = (route or "").strip() or msg_route or default_chat_route_label()
    return resolved_model, resolved_route


def insert_message_feedback(
    access_token: str,
    user_id: str,
    *,
    message_id: str,
    conversation_id: str,
    reviewer_type: str = REVIEWER_END_USER,
    feedback_reason: str | None = None,
    feedback: int | None = None,
    preference_score: int | None = None,
    model: str | None = None,
    route: str | None = None,
    prompt_version: str | None = None,
    feedback_comment: str | None = None,
    labeler_notes: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert one ``message_feedback`` row owned by ``user_id``."""
    if not feedback_persistence_enabled():
        raise ChatHistoryUnavailable()

    mid, cid = _assert_message_in_conversation(access_token, user_id, message_id, conversation_id)
    model, route = _resolve_model_route_for_feedback(
        access_token, mid, cid, model=model, route=route
    )

    if feedback is not None and feedback not in (-1, 0, 1):
        raise HTTPException(status_code=400, detail="feedback must be -1, 0, or 1")
    if preference_score is not None and not (1 <= preference_score <= 5):
        raise HTTPException(status_code=400, detail="preference_score must be between 1 and 5")

    db_reason, db_metadata = _prepare_feedback_reason_for_db(feedback_reason, metadata)

    insert: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "message_id": mid,
        "conversation_id": cid,
        "user_id": user_id,
        "reviewer_type": (reviewer_type or REVIEWER_END_USER).strip() or REVIEWER_END_USER,
        "metadata": db_metadata,
    }
    if feedback is not None:
        insert["feedback"] = feedback
    if db_reason:
        insert["feedback_reason"] = db_reason
    if preference_score is not None:
        insert["preference_score"] = preference_score
    if model and model.strip():
        insert["model"] = model.strip()
    if route and route.strip():
        insert["route"] = route.strip()
    if prompt_version and prompt_version.strip():
        insert["prompt_version"] = prompt_version.strip()
    if feedback_comment and feedback_comment.strip():
        insert["feedback_comment"] = feedback_comment.strip()
    if labeler_notes and labeler_notes.strip():
        insert["labeler_notes"] = labeler_notes.strip()

    try:
        result = (
            _table(access_token, "message_feedback")
            .insert(insert)
            .select(FEEDBACK_SELECT_COLUMNS)
            .execute()
        )
    except Exception as exc:
        _handle_db_error(exc)
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save feedback")
    return _row_to_feedback(result.data[0])
