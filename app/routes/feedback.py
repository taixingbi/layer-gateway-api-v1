"""Feedback: persist to Supabase ``message_feedback``; optionally proxy orchestrator."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.logging import log_event
from app.deps.auth_bearer import parse_bearer
from app.schemas.feedback import FeedbackRequest, FeedbackResponse
from app.services.message_feedback_service import (
    ChatHistoryUnavailable,
    feedback_persistence_enabled,
    insert_message_feedback,
)

router = APIRouter(prefix="/api", tags=["feedback"])


def _orchestrator_proxy_body(payload: FeedbackRequest) -> dict[str, Any]:
    """Legacy flat_headers orchestrator feedback JSON."""
    trace_id = payload.trace_id or (payload.metadata or {}).get("trace_id")
    if not trace_id:
        return {}
    body: dict[str, Any] = {"trace_id": trace_id, "rating": payload.rating or "thumbs_up"}
    if payload.request_id:
        body["request_id"] = payload.request_id
    if payload.feedback_type:
        body["feedback_type"] = payload.feedback_type
    if payload.feedback_comment:
        body["comment"] = payload.feedback_comment
    question = payload.question or (payload.metadata or {}).get("question")
    if question:
        body["question"] = question
    return body


@router.post("/feedback", response_model=FeedbackResponse, response_model_exclude_none=True)
async def post_feedback(request: Request, payload: FeedbackRequest):
    """Save message feedback; optionally forward to orchestrator when ``flat_headers``."""
    settings = get_settings()
    user_id = request.state.auth_context["user_id"]
    access_token = parse_bearer(request.headers.get("authorization"))

    row: dict[str, Any] | None = None
    if feedback_persistence_enabled():
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
            log_event(
                "message_feedback_saved",
                message_id=payload.message_id,
                conversation_id=payload.conversation_id,
                feedback_id=row.get("id"),
                reviewer_type=payload.reviewer_type,
                feedback_type=payload.feedback_type,
            )
        except ChatHistoryUnavailable:
            log_event("message_feedback_skipped", reason="supabase_not_configured")
        except HTTPException:
            raise
    else:
        log_event("message_feedback_skipped", reason="supabase_not_configured")

    if settings.orchestrator_contract == "flat_headers":
        proxy_body = _orchestrator_proxy_body(payload)
        if proxy_body.get("trace_id") and hasattr(request.app.state, "orchestrator_client"):
            client = request.app.state.orchestrator_client
            try:
                status_code, data = await client.post_feedback(proxy_body)
                log_event(
                    "orchestrator_feedback_proxy",
                    status=status_code,
                    trace_id=proxy_body.get("trace_id"),
                )
                if not row and status_code >= 400:
                    if data is None:
                        raise HTTPException(status_code=status_code, detail="Upstream feedback failed")
                    return JSONResponse(content=data, status_code=status_code)
            except HTTPException:
                if row:
                    pass
                else:
                    raise

    if not row:
        if settings.orchestrator_contract != "flat_headers":
            raise HTTPException(
                status_code=503,
                detail="Feedback persistence requires Supabase configuration",
            )
        return Response(status_code=204)

    return FeedbackResponse(
        id=str(row["id"]),
        message_id=str(row["message_id"]),
        conversation_id=str(row["conversation_id"]),
    )
