import json
from unittest.mock import patch

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
                "follow_up_questions": [],
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
                "follow_up_questions": [],
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        )()

    async def stream_chat(self, payload, ctx=None):
        yield 'event: token\ndata: {"text":"Hello"}\n\n'
        yield 'event: token\ndata: {"text":" world"}\n\n'


def _auth_headers():
    return {"Authorization": "Bearer token-123"}


class StubOrchestratorWithFollowUps:
    async def chat(self, payload, ctx=None):
        return type(
            "Resp",
            (),
            {
                "answer": "H4 EAD.",
                "citations": [{"cite_id": 1, "text": "visa"}],
                "follow_up_questions": [
                    "Can you explain H4 EAD?",
                    "Does renewal apply?",
                ],
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        )()

    async def stream_chat(self, payload, ctx=None):
        yield 'event: token\ndata: {"text":"Hi"}\n\n'


def test_chat_forwards_rewrite_from_upstream():
    app = create_app()

    class StubWithRewrite:
        async def chat(self, payload, ctx=None):
            return type(
                "Resp",
                (),
                {
                    "answer": "H4 EAD.",
                    "rewrite": "What is the candidate's visa status?",
                    "citations": [],
                    "follow_up_questions": [],
                    "usage": {},
                },
            )()

        async def stream_chat(self, payload, ctx=None):
            yield 'event: token\ndata: {"text":"x"}\n\n'

    with TestClient(app) as client:
        app.state.orchestrator_client = StubWithRewrite()
        response = client.post(
            "/api/chat",
            headers=_auth_headers(),
            json={"message": "visa?", "metadata": {}},
        )
        assert response.status_code == 200
        assert response.json()["rewrite"] == "What is the candidate's visa status?"


def test_chat_forwards_follow_up_questions_from_upstream():
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestrator_client = StubOrchestratorWithFollowUps()
        response = client.post(
            "/api/chat",
            headers=_auth_headers(),
            json={"message": "visa status?", "metadata": {}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["follow_up_questions"] == [
            "Can you explain H4 EAD?",
            "Does renewal apply?",
        ]


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


def _kwargs_for_event(mock_log, event: str):
    for call in mock_log.call_args_list:
        if call.args and call.args[0] == event:
            return call.kwargs
    return None


def test_chat_logs_and_request_complete_include_conversation_id_when_provided():
    with patch("app.middleware.access_log.log_event") as access_mock, patch("app.routes.chat.log_event") as chat_mock:
        app = create_app()
        with TestClient(app) as client:
            app.state.orchestrator_client = StubOrchestratorClient()
            response = client.post(
                "/api/chat",
                headers=_auth_headers(),
                json={"message": "Hi", "metadata": {}, "conversation_id": "conv-access-log-001"},
            )
        assert response.status_code == 200

    rc = None
    for call in access_mock.call_args_list:
        if call.args and call.args[0] == "request_complete":
            rc = call.kwargs
            break
    assert rc is not None
    assert rc.get("conversation_id") == "conv-access-log-001"

    for event in ("request_received", "request_validated", "orchestrator_call_started", "orchestrator_call_succeeded"):
        fields = _kwargs_for_event(chat_mock, event)
        assert fields is not None, event
        assert fields.get("conversation_id") == "conv-access-log-001"


def test_chat_passes_history_to_orchestrator_client():
    app = create_app()
    stub = CapturingOrchestratorStub()
    history = [
        {"role": "user", "content": "What is Taixing Bi US visa status?"},
        {"role": "assistant", "content": "Taixing has H4 EAD and does not need sponsorship."},
    ]
    with TestClient(app) as client:
        app.state.orchestrator_client = stub
        response = client.post(
            "/api/chat",
            headers=_auth_headers(),
            json={
                "message": "what is Taixing US visa status?",
                "conversation_id": "conv-smoke-1",
                "metadata": {},
                "history": history,
            },
        )
        assert response.status_code == 200
    assert stub.chat_payload.input.question == "what is Taixing US visa status?"
    assert [h.model_dump() for h in stub.chat_payload.input.history] == history


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


class EnrichedDoneStreamStub:
    """Simulates orchestrator client output after RAG SSE aggregation."""

    async def chat(self, *args, **kwargs):
        raise NotImplementedError

    async def stream_chat(self, *args, **kwargs):
        done = json.dumps(
            {
                "status": "success",
                "citations": [{"cite_id": 1, "source": "personal_profile", "text": "H4 EAD"}],
                "follow_up_questions": ["What does H4 EAD mean?"],
            }
        )
        yield 'event: meta\ndata: {"request_id":"req_x","trace_id":"trace_x","session_id":"sess_x"}\n\n'
        yield 'event: token\ndata: {"text":"H4 EAD."}\n\n'
        yield f"event: done\ndata: {done}\n\n"


def test_chat_stream_preserves_citations_and_follow_ups_on_done():
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestrator_client = EnrichedDoneStreamStub()
        response = client.post(
            "/api/chat",
            headers={**_auth_headers(), "Accept": "text/event-stream"},
            json={"message": "visa?", "stream": True, "metadata": {}},
        )
        assert response.status_code == 200
        body = response.text
        assert "event: done" in body
        done_line = next(ln for ln in body.splitlines() if ln.startswith("data:") and "follow_up_questions" in ln)
        payload = json.loads(done_line.split("data:", 1)[1].strip())
        assert payload["status"] == "success"
        assert len(payload["citations"]) == 1
        assert payload["follow_up_questions"] == ["What does H4 EAD mean?"]


def test_chat_streaming_contract():
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestrator_client = StubOrchestratorClient()
        response = client.post(
            "/api/chat",
            headers={**_auth_headers(), "Accept": "text/event-stream"},
            json={"message": "stream please", "conversation_id": "conv-sse-meta-001"},
        )
        assert response.status_code == 200
        body = response.text
        assert "event: meta" in body
        assert "event: token" in body
        assert "event: done" in body
        meta_line = [ln for ln in body.splitlines() if ln.startswith("data:") and "request_id" in ln][0]
        meta = json.loads(meta_line.split("data:", 1)[1].strip())
        assert meta.get("conversation_id") == "conv-sse-meta-001"


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
