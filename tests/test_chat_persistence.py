"""Tests for chat route history persistence hooks."""

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.routes.chat import _gateway_sse_chunk_token_text
from app.schemas.history import ChatHistoryMessage
from app.services.chat_history_service import default_chat_route_label


@pytest.fixture(autouse=True)
def _enable_message_status_column(monkeypatch):
    """Tests assume Supabase has a ``status`` column when persisting messages."""
    monkeypatch.setenv("CHAT_PERSIST_MESSAGE_STATUS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _auth_headers():
    return {"Authorization": "Bearer token-123"}


class StubOrchestratorClient:
    async def chat(self, payload, ctx=None):
        return type(
            "Resp",
            (),
            {
                "answer": "H4 EAD. No visa sponsorship required.",
                "rewrite": "what is taixing visa status",
                "citations": [{"source": "personal_profile"}],
                "follow_up_questions": [],
                "usage": {"input_tokens": 1, "output_tokens": 2, "model": "qwen2.5-7b"},
                "latency_ms": {"total": 1200.5, "rag": {"total": 900}},
                "route": "rag",
            },
        )()

    async def stream_chat(self, payload, ctx=None):
        yield 'event: token\ndata: {"text":"Hi"}\n\n'


class StubStreamRewriteThenAnswerClient:
    """Mimics orchestrator stream: rewrite (question) then answer tokens."""

    async def chat(self, payload, ctx=None):
        raise AssertionError("stream test should not call chat")

    async def stream_chat(self, payload, ctx=None):
        yield 'event: rewrite\ndata: {"text":"how are you?"}\n\n'
        yield 'event: token\ndata: {"text":"I\'m doing well and ready to help."}\n\n'
        yield (
            'event: done\ndata: {"status":"success","rewrite":"how are you?",'
            '"citations":[{"source":"personal_profile"}],"follow_up_questions":[],'
            '"latency_ms":{"total":900.0,"intent_router":100}}\n\n'
        )


def test_gateway_sse_chunk_token_text_ignores_rewrite():
    """Rewrite SSE frames must not contribute to assistant persistence text."""
    assert _gateway_sse_chunk_token_text('event: rewrite\ndata: {"text":"how are you?"}\n\n') is None
    assert (
        _gateway_sse_chunk_token_text('event: token\ndata: {"text":"I\'m doing well."}\n\n')
        == "I'm doing well."
    )


@patch("app.routes.chat.persistence_enabled", return_value=True)
@patch("app.routes.chat.ensure_conversation")
@patch("app.routes.chat.load_messages")
@patch("app.routes.chat.append_message")
def test_chat_persists_on_success(
    mock_append,
    mock_load,
    mock_ensure,
    _enabled,
):
    """Successful chat calls history store before and after orchestrator."""
    conv_id = str(uuid.uuid4())
    mock_ensure.return_value = conv_id
    mock_load.return_value = [ChatHistoryMessage(role="user", content="prior")]
    mock_append.return_value = None

    app = create_app()
    app.state.orchestrator_client = StubOrchestratorClient()
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        headers=_auth_headers(),
        json={"message": "Hello", "conversation_id": conv_id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == conv_id
    assert body["latency_ms"]["gateway_api"]["total"] >= 0
    assert body["latency_ms"]["orchestrator"]["total"] == 1200.5
    assert mock_ensure.called
    assert mock_load.called
    assert mock_append.call_count == 2
    roles = [call.args[3] for call in mock_append.call_args_list]
    assert roles == ["user", "assistant"]
    assert mock_append.call_args_list[0].kwargs["status"] == "complete"
    assert mock_append.call_args_list[0].kwargs.get("metadata") is None
    assert mock_append.call_args_list[1].args[4] == "H4 EAD. No visa sponsorship required."
    assert mock_append.call_args_list[1].kwargs["status"] == "complete"
    meta = mock_append.call_args_list[1].kwargs["metadata"]
    assert meta["rewrite"] == "what is taixing visa status"
    assert meta["citations"] == [{"source": "personal_profile"}]
    assert meta["model"] == "qwen2.5-7b"
    assert meta["route"] == "rag"
    assert meta["latency_ms"]["orchestrator"]["total"] == 1200.5
    assert "gateway_api" in meta["latency_ms"]


@patch("app.routes.chat.persistence_enabled", return_value=True)
@patch("app.routes.chat.ensure_conversation")
@patch("app.routes.chat.load_messages")
@patch("app.routes.chat.append_message")
def test_chat_stream_persists_assistant_without_rewrite(
    mock_append,
    mock_load,
    mock_ensure,
    _enabled,
):
    """Stream path must not prepend rewrite/question text to saved assistant message."""
    conv_id = str(uuid.uuid4())
    mock_ensure.return_value = conv_id
    mock_load.return_value = []

    app = create_app()
    app.state.orchestrator_client = StubStreamRewriteThenAnswerClient()
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        headers={**_auth_headers(), "Accept": "text/event-stream"},
        json={"message": "how are you?", "stream": True, "conversation_id": conv_id},
    )
    assert response.status_code == 200
    assert mock_append.call_count == 2
    assert mock_append.call_args_list[0].args[3] == "user"
    assert mock_append.call_args_list[0].args[4] == "how are you?"
    assert mock_append.call_args_list[1].args[3] == "assistant"
    assert mock_append.call_args_list[1].args[4] == "I'm doing well and ready to help."
    meta = mock_append.call_args_list[1].kwargs["metadata"]
    assert meta["rewrite"] == "how are you?"
    assert meta["citations"] == [{"source": "personal_profile"}]
    assert meta["route"] == default_chat_route_label()
    assert meta["latency_ms"]["orchestrator"]["total"] == 900.0
    assert "gateway_api" in meta["latency_ms"]


@patch("app.routes.chat.persistence_enabled", return_value=False)
@patch("app.routes.chat.ensure_conversation")
def test_chat_skips_persistence_when_disabled(mock_ensure, _enabled):
    """Chat still succeeds when Supabase persistence is off."""
    app = create_app()
    app.state.orchestrator_client = StubOrchestratorClient()
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        headers=_auth_headers(),
        json={"message": "Hello"},
    )
    assert response.status_code == 200
    mock_ensure.assert_not_called()
