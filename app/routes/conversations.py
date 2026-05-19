"""Conversation list and message history (Supabase)."""

from fastapi import APIRouter, Depends, Query

from app.deps.supabase_auth import token_and_claims
from app.schemas.conversations import (
    ConversationListResponse,
    ConversationMessagesResponse,
    ConversationSummary,
    StoredMessage,
)
from app.services.chat_history_service import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    list_conversations,
    list_messages_for_api,
)

router = APIRouter(prefix="/api", tags=["conversations"])


@router.get("/conversations", response_model=ConversationListResponse)
def conversations_list(
    auth: tuple[str, object] = Depends(token_and_claims),
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
):
    """List conversations for the authenticated user (newest ``updated_at`` first)."""
    token, claims = auth
    rows = list_conversations(token, claims.user_id, limit=limit)
    return ConversationListResponse(
        conversations=[ConversationSummary(**row) for row in rows],
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationMessagesResponse,
)
def conversation_messages(
    conversation_id: str,
    auth: tuple[str, object] = Depends(token_and_claims),
):
    """Return messages for one owned conversation."""
    token, claims = auth
    rows = list_messages_for_api(token, claims.user_id, conversation_id)
    return ConversationMessagesResponse(
        conversation_id=conversation_id,
        messages=[StoredMessage(**row) for row in rows],
    )
