"""Feedback submission for Supabase ``message_feedback`` persistence."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _strip_db_uuid_prefix(value: str) -> str:
    """Accept UI ids like ``db-<uuid>`` in addition to raw UUID strings."""
    s = value.strip()
    if s.startswith("db-") and len(s) > 3:
        return s[3:].strip()
    return s


def _empty_str_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


class FeedbackRequest(BaseModel):
    """Message-level feedback for Supabase; legacy fields (trace_id, rating) map into columns/metadata."""

    model_config = ConfigDict(extra="ignore")

    message_id: str | None = Field(default=None, max_length=128)
    conversation_id: str | None = Field(default=None, max_length=128)
    reviewer_type: str = Field(default="end_user", min_length=1, max_length=64)
    feedback_type: str | None = Field(default=None, max_length=128)
    feedback: int | None = Field(default=None, ge=-1, le=1)
    preference_score: int | None = Field(default=None, ge=1, le=5)
    model: str | None = Field(default=None, max_length=128)
    route: str | None = Field(default=None, max_length=64)
    prompt_version: str | None = Field(default=None, max_length=64)
    feedback_comment: str | None = Field(default=None, max_length=4000)
    labeler_notes: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] | None = None

    # Legacy / correlation (stored in metadata when set)
    trace_id: str | None = Field(default=None, max_length=128)
    request_id: str | None = Field(default=None, max_length=128)
    rating: Literal["thumbs_up", "thumbs_down"] | None = None
    comment: str | None = Field(default=None, max_length=4000)
    question: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="before")
    @classmethod
    def _coerce_incoming(cls, data: Any) -> Any:
        """Map BFF/UI aliases and tolerate empty strings / null metadata."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if not d.get("message_id") and d.get("messageId"):
            d["message_id"] = d.pop("messageId")
        if not d.get("conversation_id") and d.get("conversationId"):
            d["conversation_id"] = d.pop("conversationId")
        run_id = d.pop("run_id", None)
        if run_id and not d.get("trace_id"):
            d["trace_id"] = run_id
        raw_feedback = d.get("feedback")
        if isinstance(raw_feedback, str) and raw_feedback in ("thumbs_up", "thumbs_down"):
            d.setdefault("rating", raw_feedback)
            d.pop("feedback", None)
        for key in ("trace_id", "request_id"):
            if key in d:
                d[key] = _empty_str_to_none(d[key])
        if d.get("metadata") is None:
            d["metadata"] = {}
        for id_key in ("message_id", "conversation_id"):
            val = d.get(id_key)
            if isinstance(val, str):
                d[id_key] = _strip_db_uuid_prefix(val)
        return d

    @field_validator("message_id", "conversation_id", mode="before")
    @classmethod
    def _normalize_ids(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = _strip_db_uuid_prefix(value)
            return stripped if stripped else None
        return value

    @field_validator("trace_id", "request_id", mode="before")
    @classmethod
    def _normalize_optional_ids(cls, value: Any) -> Any:
        return _empty_str_to_none(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _ensure_metadata_dict(self) -> "FeedbackRequest":
        if self.metadata is None:
            return self.model_copy(update={"metadata": {}})
        return self

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
