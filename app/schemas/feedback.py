"""Feedback submission payload for orchestrator proxy."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class FeedbackRequest(BaseModel):
    """Thumbs up/down feedback tied to trace and optional request id."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    request_id: str | None = None
    rating: Literal["thumbs_up", "thumbs_down"]
    feedback_type: str | None = None
    comment: str | None = None
    question: str | None = None
