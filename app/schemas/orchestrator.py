"""Orchestrator upstream request and response Pydantic models."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.history import ChatHistoryMessage


class AuthContext(BaseModel):
    """Trusted user identity passed to orchestrator (from middleware)."""

    user_id: str
    tenant_id: str
    roles: list[str] = Field(default_factory=list)


class OrchestratorContext(BaseModel):
    """Correlation and session fields for orchestrator calls."""

    session_id: str
    conversation_id: str | None = None
    request_id: str
    trace_id: str


class OrchestratorInput(BaseModel):
    """User question and optional conversation history."""

    question: str
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=100)


class OrchestratorClientInfo(BaseModel):
    """Client metadata (source app, page, arbitrary metadata)."""

    source: str = "nextjs-web"
    page: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrchestratorChatRequest(BaseModel):
    """Full gateway_json orchestrator POST body."""

    model_config = ConfigDict(extra="forbid")

    auth: AuthContext
    context: OrchestratorContext
    input: OrchestratorInput
    client: OrchestratorClientInfo


class OrchestratorChatResponse(BaseModel):
    """Parsed orchestrator non-stream JSON response."""

    model_config = ConfigDict(extra="allow")

    answer: str
    rewrite: str | None = None
    route: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    timings_ms: dict[str, Any] | None = None
