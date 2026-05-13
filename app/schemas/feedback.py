from typing import Literal

from pydantic import BaseModel, ConfigDict


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    request_id: str | None = None
    rating: Literal["thumbs_up", "thumbs_down"]
    feedback_type: str | None = None
    comment: str | None = None
    question: str | None = None
