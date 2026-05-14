import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.orchestrator_call_context import OrchestratorCallContext


class CapturingOrchestratorStub:
    """Records last ``chat`` / ``stream_chat`` arguments for contract assertions."""

    def __init__(self):
        self.chat_payload = None
        self.chat_ctx: OrchestratorCallContext | None = None
        self.stream_payload = None
        self.stream_ctx: OrchestratorCallContext | None = None

    async def chat(self, payload, ctx=None):
        self.chat_payload = payload
        self.chat_ctx = ctx
        return type(
            "Resp",
            (),
            {
                "answer": "ok",
                "citations": [],
                "usage": {"input_tokens": 1, "output_tokens": 2},
            },
        )()

    async def stream_chat(self, payload, ctx=None):
        self.stream_payload = payload
        self.stream_ctx = ctx
        yield 'event: token\ndata: {"text":"x"}\n\n'


class StubOrchestratorClient:
    async def chat(self, payload, ctx=None):
        return type(
            "Resp",
            (),
            {
                "answer": "You can return items within 30 days.",
                "citations": [],
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        )()

    async def stream_chat(self, payload, ctx=None):
        yield 'event: token\ndata: {"text":"Hello"}\n\n'
        yield 'event: token\ndata: {"text":" world"}\n\n'


def _auth_headers():
    return {"Authorization": "Bearer token-123"}


def test_chat_returns_stable_response_contract():
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestrator_client = StubOrchestratorClient()
        response = client.post(
            "/api/chat",
            headers=_auth_headers(),
            json={"message": "What is the return policy?", "metadata": {"page": "/support"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["answer"] == "You can return items within 30 days."
        assert data["request_id"].startswith("req_")
        assert data["trace_id"].startswith("trace_")
        assert data["session_id"].startswith("sess_")
        assert "error" not in data


def test_chat_uses_x_session_id_header_when_present():
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestrator_client = StubOrchestratorClient()
        response = client.post(
            "/api/chat",
            headers={**_auth_headers(), "X-Session-Id": "ses-from-header-99"},
            json={"message": "What is the return policy?", "metadata": {"page": "/support"}},
        )
        assert response.status_code == 200
        assert response.json()["session_id"] == "ses-from-header-99"


def test_chat_rejects_session_id_in_json_body():
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestrator_client = StubOrchestratorClient()
        response = client.post(
            "/api/chat",
            headers=_auth_headers(),
            json={"session_id": "sess-from-body", "message": "Hi", "metadata": {}},
        )
    assert response.status_code == 422


def test_chat_passes_conversation_id_to_orchestrator_client_from_json_body():
    app = create_app()
    stub = CapturingOrchestratorStub()
    with TestClient(app) as client:
        app.state.orchestrator_client = stub
        response = client.post(
            "/api/chat",
            headers=_auth_headers(),
            json={"message": "Hi", "metadata": {}, "conversation_id": "conv-from-json-001"},
        )
        assert response.status_code == 200
    assert stub.chat_payload.context.conversation_id == "conv-from-json-001"
    assert stub.chat_ctx is not None
    assert stub.chat_ctx.conversation_id == "conv-from-json-001"


def test_chat_passes_conversation_id_from_x_conversation_id_header():
    app = create_app()
    stub = CapturingOrchestratorStub()
    with TestClient(app) as client:
        app.state.orchestrator_client = stub
        response = client.post(
            "/api/chat",
            headers={**_auth_headers(), "X-Conversation-Id": "conv-from-header-002"},
            json={"message": "Hi", "metadata": {}},
        )
        assert response.status_code == 200
    assert stub.chat_payload.context.conversation_id == "conv-from-header-002"
    assert stub.chat_ctx.conversation_id == "conv-from-header-002"


def test_chat_x_conversation_id_header_overrides_json_body():
    app = create_app()
    stub = CapturingOrchestratorStub()
    with TestClient(app) as client:
        app.state.orchestrator_client = stub
        response = client.post(
            "/api/chat",
            headers={**_auth_headers(), "X-Conversation-Id": "conv-header-wins"},
            json={"message": "Hi", "metadata": {}, "conversation_id": "conv-json-ignored"},
        )
        assert response.status_code == 200
    assert stub.chat_payload.context.conversation_id == "conv-header-wins"
    assert stub.chat_ctx.conversation_id == "conv-header-wins"


def test_chat_stream_passes_conversation_id_on_ctx():
    app = create_app()
    stub = CapturingOrchestratorStub()
    with TestClient(app) as client:
        app.state.orchestrator_client = stub
        response = client.post(
            "/api/chat",
            headers=_auth_headers(),
            json={"message": "s", "stream": True, "metadata": {}, "conversation_id": "conv-stream-003"},
        )
        assert response.status_code == 200
    assert stub.stream_payload.context.conversation_id == "conv-stream-003"
    assert stub.stream_ctx is not None
    assert stub.stream_ctx.conversation_id == "conv-stream-003"


def test_chat_rejects_short_x_conversation_id():
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestrator_client = StubOrchestratorClient()
        response = client.post(
            "/api/chat",
            headers={**_auth_headers(), "X-Conversation-Id": "ab"},
            json={"message": "Hi", "metadata": {}},
        )
        assert response.status_code == 400


def test_chat_rejects_short_x_session_id():
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestrator_client = StubOrchestratorClient()
        response = client.post(
            "/api/chat",
            headers={**_auth_headers(), "X-Session-Id": "ab"},
            json={"message": "Hi", "metadata": {}},
        )
        assert response.status_code == 400


def test_chat_requires_auth():
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": "Hello"})
        assert response.status_code == 401


def test_chat_validation_rejects_blank_message():
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestrator_client = StubOrchestratorClient()
        response = client.post(
            "/api/chat",
            headers=_auth_headers(),
            json={"message": "   "},
        )
        assert response.status_code == 400


def test_chat_streaming_contract():
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestrator_client = StubOrchestratorClient()
        response = client.post(
            "/api/chat",
            headers={**_auth_headers(), "Accept": "text/event-stream"},
            json={"message": "stream please"},
        )
        assert response.status_code == 200
        body = response.text
        assert "event: meta" in body
        assert "event: token" in body
        assert "event: done" in body


def test_chat_streaming_via_json_body_stream_flag():
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestrator_client = StubOrchestratorClient()
        response = client.post(
            "/api/chat",
            headers=_auth_headers(),
            json={"message": "stream via body", "stream": True, "metadata": {}},
        )
        assert response.status_code == 200
        body = response.text
        assert "event: meta" in body
        assert "event: token" in body
        assert "event: done" in body


class StubOrchestratorContextErrorInStream:
    async def chat(self, *args, **kwargs):
        raise NotImplementedError

    async def stream_chat(self, *args, **kwargs):
        yield 'event: token\ndata: {"text":"partial answer"}\n\n'
        bad = (
            "Error: ValueError: <Token var=<ContextVar name='pipeline_phase' at 0x0> "
            "at 0x0> was created in a different Context"
        )
        yield f"event: token\ndata: {json.dumps({'text': bad})}\n\n"


def test_chat_stream_rewrites_contextvar_poison_token_to_sse_error():
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestrator_client = StubOrchestratorContextErrorInStream()
        response = client.post(
            "/api/chat",
            headers=_auth_headers(),
            json={"message": "x", "stream": True, "metadata": {}},
        )
        assert response.status_code == 200
        body = response.text
        assert "event: error" in body
        assert "upstream_internal" in body
        assert 'data: {"status":"error"}' in body
        assert "different Context" not in body
