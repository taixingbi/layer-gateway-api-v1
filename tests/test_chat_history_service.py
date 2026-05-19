"""Unit tests for chat history merge and conversation helpers."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.schemas.history import ChatHistoryMessage
from app.services.chat_history_service import (
    ensure_conversation,
    merge_history,
    _validate_uuid,
)


def test_merge_history_db_canonical_with_client_tail():
    """Client turns beyond DB length are appended."""
    db = [
        ChatHistoryMessage(role="user", content="a"),
        ChatHistoryMessage(role="assistant", content="b"),
    ]
    client = db + [ChatHistoryMessage(role="user", content="c")]
    merged = merge_history(db, client)
    assert len(merged) == 3
    assert merged[-1].content == "c"


def test_merge_history_returns_db_when_client_shorter():
    """Shorter client history does not replace DB."""
    db = [
        ChatHistoryMessage(role="user", content="a"),
        ChatHistoryMessage(role="assistant", content="b"),
    ]
    client = [ChatHistoryMessage(role="user", content="x")]
    assert merge_history(db, client) == db


def test_validate_uuid_rejects_invalid():
    """Non-UUID conversation ids raise 400."""
    with pytest.raises(HTTPException) as exc:
        _validate_uuid("conv_not_uuid")
    assert exc.value.status_code == 400


@patch("app.services.chat_history_service.persistence_enabled", return_value=True)
@patch("app.services.chat_history_service._table")
def test_ensure_conversation_creates_when_missing(mock_table, _enabled):
    """Missing conversation_id mints a new row."""
    mock_insert = MagicMock()
    mock_insert.execute.return_value = MagicMock(data=[{"id": "unused"}])
    mock_table.return_value.insert.return_value = mock_insert

    result = ensure_conversation("tok", "user-1", None, first_message="Hello there")
    uuid.UUID(result)
    mock_table.return_value.insert.assert_called_once()
    insert_row = mock_table.return_value.insert.call_args[0][0]
    assert insert_row["id"] == result
    assert insert_row["user_id"] == "user-1"


@patch("app.services.chat_history_service.persistence_enabled", return_value=True)
@patch("app.services.chat_history_service._table")
def test_ensure_conversation_404_when_not_owned(mock_table, _enabled):
    """Unknown conversation for user returns 404."""
    cid = str(uuid.uuid4())
    mock_select = MagicMock()
    mock_select.execute.return_value = MagicMock(data=[])
    chain = mock_table.return_value.select.return_value
    chain.eq.return_value = chain
    chain.limit.return_value = mock_select

    with pytest.raises(HTTPException) as exc:
        ensure_conversation("tok", "user-1", cid, first_message="hi")
    assert exc.value.status_code == 404
