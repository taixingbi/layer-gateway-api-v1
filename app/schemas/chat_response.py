from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ErrorDetails(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="success")
    session_id: str
    request_id: str
    trace_id: str
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    error: ErrorDetails | None = None
