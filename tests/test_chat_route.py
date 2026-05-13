from fastapi.testclient import TestClient

from app.main import create_app


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
        assert "error" in data and data["error"] is None


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
            "/api/chat?stream=true",
            headers={**_auth_headers(), "Accept": "text/event-stream"},
            json={"message": "stream please"},
        )
        assert response.status_code == 200
        body = response.text
        assert "event: meta" in body
        assert "event: token" in body
        assert "event: done" in body
