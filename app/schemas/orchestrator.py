from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.history import ChatHistoryMessage


class AuthContext(BaseModel):
    user_id: str
    tenant_id: str
    roles: list[str] = Field(default_factory=list)


class OrchestratorContext(BaseModel):
    session_id: str
    conversation_id: str | None = None
    request_id: str
    trace_id: str


class OrchestratorInput(BaseModel):
    question: str
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=100)


class OrchestratorClientInfo(BaseModel):
    source: str = "nextjs-web"
    page: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrchestratorChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth: AuthContext
    context: OrchestratorContext
    input: OrchestratorInput
    client: OrchestratorClientInfo


class OrchestratorChatResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    answer: str
    rewrite: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
