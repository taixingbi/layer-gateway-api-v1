from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str | None = Field(default=None, min_length=3, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    stream: bool = False
    client_timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
