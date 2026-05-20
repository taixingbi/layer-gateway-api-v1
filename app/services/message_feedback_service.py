"""Supabase ``message_feedback`` persistence for chat message ratings and annotations."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException

from app.services.chat_history_service import (
    ChatHistoryUnavailable,
    _assert_conversation_owned,
    _handle_db_error,
    _table,
    _validate_uuid,
    persistence_enabled,
)
from app.services.time_util import format_iso_est

FEEDBACK_COLUMNS = (
    "id,message_id,conversation_id,user_id,feedback,feedback_type,preference_score,"
    "reviewer_type,model,prompt_version,route,feedback_comment,labeler_notes,metadata,created_at"
)

REVIEWER_END_USER = "end_user"
RATING_TO_FEEDBACK = {"thumbs_up": 1, "thumbs_down": -1}
RATING_TO_PREFERENCE_DEFAULT = {"thumbs_up": 5, "thumbs_down": 1}


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
        "feedback_type",
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
    return out


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


def insert_message_feedback(
    access_token: str,
    user_id: str,
    *,
    message_id: str,
    conversation_id: str,
    reviewer_type: str = REVIEWER_END_USER,
    feedback_type: str | None = None,
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

    if feedback is not None and feedback not in (-1, 0, 1):
        raise HTTPException(status_code=400, detail="feedback must be -1, 0, or 1")
    if preference_score is not None and not (1 <= preference_score <= 5):
        raise HTTPException(status_code=400, detail="preference_score must be between 1 and 5")

    insert: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "message_id": mid,
        "conversation_id": cid,
        "user_id": user_id,
        "reviewer_type": (reviewer_type or REVIEWER_END_USER).strip() or REVIEWER_END_USER,
        "metadata": metadata or {},
    }
    if feedback is not None:
        insert["feedback"] = feedback
    if feedback_type:
        insert["feedback_type"] = feedback_type.strip()
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
            .select("id,message_id,conversation_id,user_id,feedback,feedback_type,preference_score,reviewer_type,model,route,prompt_version,feedback_comment,labeler_notes,metadata,created_at")
            .execute()
        )
    except Exception as exc:
        _handle_db_error(exc)
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save feedback")
    return _row_to_feedback(result.data[0])
