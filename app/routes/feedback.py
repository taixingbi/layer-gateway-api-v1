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

router = APIRouter(prefix="/api", tags=["feedback"])


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

    if not payload.message_id or not payload.conversation_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "message_id and conversation_id are required to save feedback. "
                "Wait until the assistant reply is saved, then try again."
            ),
        )

    try:
        row = insert_message_feedback(
            access_token,
            user_id,
            message_id=payload.message_id,
            conversation_id=payload.conversation_id,
            reviewer_type=payload.reviewer_type,
            feedback_type=payload.feedback_type,
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
        message_id=payload.message_id,
        conversation_id=payload.conversation_id,
        feedback_id=row.get("id"),
        reviewer_type=payload.reviewer_type,
        feedback_type=payload.feedback_type,
    )

    return FeedbackResponse(
        id=str(row["id"]),
        message_id=str(row["message_id"]),
        conversation_id=str(row["conversation_id"]),
    )
