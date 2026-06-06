"""Ensure persisted conversation_id is not cleared before stream meta."""

import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.history import ChatHistoryMessage


class StubStreamClient:
    async def chat(self, payload, ctx=None):
        raise AssertionError("stream test")

    async def stream_chat(self, payload, ctx=None):
        yield 'event: answer_delta\ndata: {"text":"Hi"}\n\n'
        yield 'event: done\ndata: {"status":"success"}\n\n'


@patch("app.routes.chat.persistence_enabled", return_value=True)
@patch("app.routes.chat.ensure_conversation")
@patch("app.routes.chat.load_messages")
@patch("app.routes.chat.append_message")
def test_stream_meta_includes_persisted_conversation_id(
    mock_append,
    mock_load,
    mock_ensure,
    _enabled,
):
    """New thread UUID from persistence must appear in first SSE meta."""
    conv_id = str(uuid.uuid4())
    mock_ensure.return_value = conv_id
    mock_load.return_value = []
    mock_append.return_value = str(uuid.uuid4())

    app = create_app()
    app.state.orchestrator_client = StubStreamClient()
    client = TestClient(app)

    response = client.post(
        "/v1/chat",
        headers={"Authorization": "Bearer token-123", "Accept": "text/event-stream"},
        json={"message": "Hello", "stream": True},
    )
    assert response.status_code == 200
    assert f'"conversation_id": "{conv_id}"' in response.text
