"""Unit tests for chat history merge and conversation helpers."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.schemas.history import ChatHistoryMessage
from app.services.chat_history_service import (
    MESSAGE_STATUS_COMPLETE,
    append_message,
    assistant_message_metadata,
    ensure_conversation,
    merge_history,
    orchestrator_timings_ms,
    resolve_assistant_model_name,
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


def test_orchestrator_timings_ms_extracts_dict():
    payload = {"latency_ms": {"total": 100.5, "rag": {"total": 80}}}
    assert orchestrator_timings_ms(payload) == payload["latency_ms"]


def test_assistant_message_metadata_includes_latency_ms():
    latency = {
        "total": 4000,
        "auth": 10,
        "validation": 0,
        "storage": {"total": 50, "write_user_message": 30, "write_assistant_message": 20},
        "orchestrator": {
            "proxy_total": 3940,
            "workflow": {"total": 3632.46, "rag": {"total": 2367.0}},
        },
    }
    meta = assistant_message_metadata(
        rewrite="visa",
        latency_ms=latency,
    )
    assert meta is not None
    assert meta["latency_ms"] == latency


def test_assistant_message_metadata_includes_usage():
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "intent_router": {"total_tokens": 40},
    }
    meta = assistant_message_metadata(usage=usage)
    assert meta is not None
    assert meta["usage"]["prompt_tokens"] == 100


@patch("app.core.config.get_settings")
def test_resolve_assistant_model_name_prefers_env_when_no_upstream(mock_settings):
    mock_settings.return_value.chat_assistant_model = "qwen2.5-7b"
    assert resolve_assistant_model_name() == "qwen2.5-7b"


def test_assistant_message_metadata_omits_empty_fields():
    """Metadata always includes default route; skips blank rewrite."""
    meta_empty = assistant_message_metadata(rewrite="  ")
    assert meta_empty is not None
    assert "route" in meta_empty
    meta = assistant_message_metadata(
        rewrite="visa status",
        citations=[{"source": "personal_profile"}],
        model="qwen2.5-7b",
        route="rag",
    )
    assert meta == {
        "rewrite": "visa status",
        "citations": [{"source": "personal_profile"}],
        "model": "qwen2.5-7b",
        "route": "rag",
    }


@patch("app.services.chat_history_service.persistence_enabled", return_value=True)
@patch("app.services.chat_history_service._assert_conversation_owned")
@patch("app.services.chat_history_service._table")
def test_append_message_writes_status_and_metadata(mock_table, _owned, _enabled):
    """Insert row includes status and metadata columns."""
    cid = str(uuid.uuid4())
    mock_insert = MagicMock()
    mock_insert.execute.return_value = MagicMock(data=[])
    mock_table.return_value.insert.return_value = mock_insert

    append_message(
        "tok",
        "user-1",
        cid,
        "assistant",
        "H4 EAD.",
        status=MESSAGE_STATUS_COMPLETE,
        metadata={"rewrite": "visa?", "citations": []},
    )  # status passed explicitly for insert assertion
    insert_row = mock_table.return_value.insert.call_args[0][0]
    assert insert_row["status"] == MESSAGE_STATUS_COMPLETE
    assert insert_row["metadata"]["rewrite"] == "visa?"


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
