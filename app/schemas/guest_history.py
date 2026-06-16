"""Schemas for admin guest chat audit API."""

from typing import Any

from pydantic import BaseModel, Field


class GuestChatEvent(BaseModel):
    id: str | None = None
    created_at: str | None = None
    auth_guest: bool = True
    user_type: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
    request_id: str | None = None
    conversation_id: str | None = None
    prompt: str
    prompt_chars: int | None = None
    route: str | None = None
    answer_preview: str | None = None
    latency_ms: dict[str, Any] | None = None
    client_ip: str | None = None
    user_agent: str | None = None


class GuestHistoryResponse(BaseModel):
    events: list[GuestChatEvent] = Field(default_factory=list)
