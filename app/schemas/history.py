from typing import Literal

from pydantic import BaseModel, Field


class ChatHistoryMessage(BaseModel):
    """One turn in a multi-turn chat passed to the orchestrator."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)
