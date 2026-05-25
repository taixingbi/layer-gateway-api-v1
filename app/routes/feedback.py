"""Feedback: persist to Supabase ``message_feedback`` only (no orchestrator forward)."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.logging import log_event
from app.deps.auth_bearer import parse_bearer
from app.schemas.feedback import FeedbackRequest, FeedbackResponse
from app.services.message_feedback_service import (
    ChatHistoryUnavailable,
    feedback_persistence_enabled,
    insert_message_feedback,
)

router = APIRouter(prefix="/v1", tags=["feedback"])


def _conversation_id_from_header(request: Request) -> str | None:
    """``X-Conversation-Id`` fallback (same as ``POST /v1/chat``)."""
    raw = (request.headers.get("x-conversation-id") or "").strip()
    return raw or None


def _resolve_feedback_ids(
    request: Request, payload: FeedbackRequest
) -> tuple[str | None, str | None]:
    """Body ids first; conversation may come from ``X-Conversation-Id``."""
    message_id = payload.message_id
    conversation_id = payload.conversation_id or _conversation_id_from_header(request)
    return message_id, conversation_id


@router.post("/feedback", response_model=FeedbackResponse, response_model_exclude_none=True)
async def post_feedback(request: Request, payload: FeedbackRequest):
    """Save message feedback to Supabase."""
    user_id = request.state.auth_context["user_id"]
    access_token = parse_bearer(request.headers.get("authorization"))

    if not feedback_persistence_enabled():
        raise HTTPException(
            status_code=503,
            detail="Feedback persistence requires Supabase configuration",
        )

    message_id, conversation_id = _resolve_feedback_ids(request, payload)
    if not message_id or not conversation_id:
        missing = []
        if not message_id:
            missing.append("message_id")
        if not conversation_id:
            missing.append("conversation_id")
        legacy_only = bool(payload.trace_id or payload.rating) and not message_id and not conversation_id
        log_event(
            "feedback_rejected",
            missing=",".join(missing),
            legacy_trace_only=legacy_only,
            has_trace_id=bool(payload.trace_id),
            has_rating=bool(payload.rating),
        )
        hint = (
            "Redeploy layer-web-v1 (feedback BFF must send message_id + conversation_id) "
            "and hard-refresh the browser."
            if legacy_only
            else "Wait until the assistant reply finishes saving, then try again."
        )
        raise HTTPException(
            status_code=400,
            detail=f"{', '.join(missing)} required to save feedback. {hint}",
        )

    try:
        row = insert_message_feedback(
            access_token,
            user_id,
            message_id=message_id,
            conversation_id=conversation_id,
            reviewer_type=payload.reviewer_type,
            feedback_reason=payload.feedback_reason,
            feedback=payload.feedback,
            preference_score=payload.preference_score,
            model=payload.model,
            route=payload.route,
            prompt_version=payload.prompt_version,
            feedback_comment=payload.feedback_comment,
            labeler_notes=payload.labeler_notes,
            metadata=payload.metadata or None,
        )
    except ChatHistoryUnavailable:
        raise HTTPException(
            status_code=503,
            detail="Feedback persistence requires Supabase configuration",
        ) from None
    except HTTPException:
        raise

    log_event(
        "message_feedback_saved",
        message_id=message_id,
        conversation_id=conversation_id,
        feedback_id=row.get("id"),
        reviewer_type=payload.reviewer_type,
        feedback_reason=payload.feedback_reason,
    )

    return FeedbackResponse(
        id=str(row["id"]),
        message_id=str(row["message_id"]),
        conversation_id=str(row["conversation_id"]),
    )
