"""Inbound chat request body from the web client."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.history import ChatHistoryMessage


class ChatRequest(BaseModel):
    """Validated chat POST payload (message, optional history, stream flag)."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str | None = Field(default=None, min_length=3, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=100)
    stream: bool = True
    client_timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
