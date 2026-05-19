"""Gateway conversation list and message response models."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConversationSummary(BaseModel):
    """One row in GET /api/conversations."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ConversationListResponse(BaseModel):
    """List of conversations for the authenticated user."""

    model_config = ConfigDict(extra="forbid")

    conversations: list[ConversationSummary] = Field(default_factory=list)


class StoredMessage(BaseModel):
    """One persisted message."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    role: str
    content: str
    created_at: str | None = None


class ConversationMessagesResponse(BaseModel):
    """Messages for a single conversation."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    messages: list[StoredMessage] = Field(default_factory=list)
