"""Gateway chat JSON response models."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Usage(BaseModel):
    """Token usage summary from orchestrator."""

    input_tokens: int = 0
    output_tokens: int = 0


class ErrorDetails(BaseModel):
    """Structured error payload for failed chat responses."""

    code: str
    message: str
    details: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    """Successful or error chat response returned to the web client."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="success")
    session_id: str
    request_id: str
    trace_id: str
    conversation_id: str | None = None
    assistant_message_id: str | None = None
    answer: str
    rewrite: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    timings_ms: dict[str, Any] | None = None
    error: ErrorDetails | None = None
