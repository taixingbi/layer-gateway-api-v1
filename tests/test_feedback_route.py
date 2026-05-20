"""Feedback route tests (Supabase persistence + optional orchestrator proxy)."""

import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def _auth_headers():
    return {"Authorization": "Bearer token-123"}


class StubWithFeedback:
    """Orchestrator stub that records feedback POST body."""

    async def chat(self, *args, **kwargs):
        raise NotImplementedError

    async def stream_chat(self, *args, **kwargs):
        raise NotImplementedError

    async def post_feedback(self, body):
        assert body["trace_id"] == "req-123"
        return 200, {"ok": True}


@patch("app.routes.feedback.feedback_persistence_enabled", return_value=True)
@patch("app.routes.feedback.insert_message_feedback")
def test_feedback_persists_and_proxies_flat_headers(mock_insert, _enabled, monkeypatch):
    """Persist to Supabase and proxy orchestrator when flat_headers."""
    monkeypatch.setenv("ORCHESTRATOR_CONTRACT", "flat_headers")
    get_settings.cache_clear()

    cid = str(uuid.uuid4())
    mid = str(uuid.uuid4())
    mock_insert.return_value = {
        "id": str(uuid.uuid4()),
        "message_id": mid,
        "conversation_id": cid,
    }

    app = create_app()
    app.state.orchestrator_client = StubWithFeedback()
    client = TestClient(app)
    response = client.post(
        "/api/feedback",
        headers=_auth_headers(),
        json={
            "message_id": mid,
            "conversation_id": cid,
            "trace_id": "req-123",
            "request_id": "req-123",
            "rating": "thumbs_up",
        },
    )
    assert response.status_code == 200
    assert response.json()["message_id"] == mid
    get_settings.cache_clear()


def test_feedback_requires_auth():
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/feedback",
            json={
                "message_id": str(uuid.uuid4()),
                "conversation_id": str(uuid.uuid4()),
                "rating": "thumbs_up",
                "trace_id": "x",
            },
        )
    assert response.status_code == 401
