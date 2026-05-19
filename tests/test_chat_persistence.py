"""Tests for chat route history persistence hooks."""

import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.history import ChatHistoryMessage


def _auth_headers():
    return {"Authorization": "Bearer token-123"}


class StubOrchestratorClient:
    async def chat(self, payload, ctx=None):
        return type(
            "Resp",
            (),
            {
                "answer": "Assistant reply",
                "citations": [],
                "follow_up_questions": [],
                "usage": {"input_tokens": 1, "output_tokens": 2},
            },
        )()

    async def stream_chat(self, payload, ctx=None):
        yield 'event: token\ndata: {"text":"Hi"}\n\n'


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
    assert mock_ensure.called
    assert mock_load.called
    assert mock_append.call_count == 2
    roles = [call.args[3] for call in mock_append.call_args_list]
    assert roles == ["user", "assistant"]


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
