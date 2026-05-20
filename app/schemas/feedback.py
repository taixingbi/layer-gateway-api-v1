"""Feedback submission for message_feedback persistence and optional orchestrator proxy."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FeedbackRequest(BaseModel):
    """Message-level feedback (Supabase) with optional legacy orchestrator fields."""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=3, max_length=128)
    conversation_id: str = Field(min_length=3, max_length=128)
    reviewer_type: str = Field(default="end_user", min_length=1, max_length=64)
    feedback_type: str | None = Field(default=None, max_length=128)
    feedback: int | None = Field(default=None, ge=-1, le=1)
    preference_score: int | None = Field(default=None, ge=1, le=5)
    model: str | None = Field(default=None, max_length=128)
    route: str | None = Field(default=None, max_length=64)
    prompt_version: str | None = Field(default=None, max_length=64)
    feedback_comment: str | None = Field(default=None, max_length=4000)
    labeler_notes: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Legacy orchestrator proxy (optional when persisting to Supabase)
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)
    request_id: str | None = Field(default=None, max_length=128)
    rating: Literal["thumbs_up", "thumbs_down"] | None = None
    comment: str | None = Field(default=None, max_length=4000)
    question: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _normalize_legacy_fields(self) -> "FeedbackRequest":
        """Map thumbs UI fields into feedback columns when not set explicitly."""
        updates: dict[str, Any] = {}
        if self.feedback is None and self.rating is not None:
            updates["feedback"] = 1 if self.rating == "thumbs_up" else -1
        if not self.feedback_type and self.rating:
            updates["feedback_type"] = self.rating
        if self.preference_score is None and self.rating:
            updates["preference_score"] = 5 if self.rating == "thumbs_up" else 1
        if not self.feedback_comment and self.comment:
            updates["feedback_comment"] = self.comment
        meta = dict(self.metadata)
        if self.question and "question" not in meta:
            meta["question"] = self.question
        if self.trace_id and "trace_id" not in meta:
            meta["trace_id"] = self.trace_id
        if self.request_id and "request_id" not in meta:
            meta["request_id"] = self.request_id
        if meta != self.metadata:
            updates["metadata"] = meta
        if updates:
            return self.model_copy(update=updates)
        return self


class FeedbackResponse(BaseModel):
    """Created feedback row returned to clients."""

    model_config = ConfigDict(extra="forbid")

    id: str
    message_id: str
    conversation_id: str
    status: str = "created"
