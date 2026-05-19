"""Feedback proxy route tests (flat_headers contract)."""

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def _auth_headers():
    """Bearer header matching conftest fake Supabase verify."""
    return {"Authorization": "Bearer token-123"}


class StubWithFeedback:
    """Orchestrator stub that records feedback POST body."""
    async def chat(self, *args, **kwargs):
        """Chat."""
        raise NotImplementedError

    async def stream_chat(self, *args, **kwargs):
        """Stream chat."""
        raise NotImplementedError

    async def post_feedback(self, body):
        """Post feedback."""
        assert body["trace_id"] == "req-123"
        return 200, {"ok": True}


def test_feedback_returns_501_when_contract_is_gateway_json(monkeypatch):
    """Feedback returns 501 when contract is gateway json."""
    monkeypatch.setenv("ORCHESTRATOR_CONTRACT", "gateway_json")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/feedback",
            headers=_auth_headers(),
            json={"trace_id": "req-123", "rating": "thumbs_up"},
        )
    assert response.status_code == 501
    get_settings.cache_clear()


def test_feedback_requires_auth():
    """Feedback requires auth."""
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/api/feedback", json={"trace_id": "x", "rating": "thumbs_up"})
    assert response.status_code == 401


def test_feedback_proxies_when_flat_headers(monkeypatch):
    """Feedback proxies when flat headers."""
    monkeypatch.setenv("ORCHESTRATOR_CONTRACT", "flat_headers")
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestrator_client = StubWithFeedback()
        response = client.post(
            "/api/feedback",
            headers=_auth_headers(),
            json={"trace_id": "req-123", "request_id": "req-123", "rating": "thumbs_up"},
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
