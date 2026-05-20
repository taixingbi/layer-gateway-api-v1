"""Feedback route tests (Supabase persistence only)."""

import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def _auth_headers():
    return {"Authorization": "Bearer token-123"}


@patch("app.routes.feedback.feedback_persistence_enabled", return_value=True)
@patch("app.routes.feedback.insert_message_feedback")
def test_feedback_persists(mock_insert, _enabled):
    """POST /api/feedback saves to Supabase and does not call orchestrator."""
    cid = str(uuid.uuid4())
    mid = str(uuid.uuid4())
    mock_insert.return_value = {
        "id": str(uuid.uuid4()),
        "message_id": mid,
        "conversation_id": cid,
    }

    orch = MagicMock()
    orch.post_feedback = MagicMock()

    app = create_app()
    app.state.orchestrator_client = orch
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
    orch.post_feedback.assert_not_called()


@patch("app.routes.feedback.feedback_persistence_enabled", return_value=True)
def test_feedback_trace_only_without_message_ids_returns_400(_enabled):
    """Legacy trace_id-only body is rejected; message scope is required."""
    app = create_app()
    client = TestClient(app)
    response = client.post(
        "/api/feedback",
        headers=_auth_headers(),
        json={"trace_id": "req-123", "rating": "thumbs_up"},
    )
    assert response.status_code == 400
    assert "message_id" in response.json()["detail"]


@patch("app.routes.feedback.feedback_persistence_enabled", return_value=True)
def test_feedback_missing_ids_returns_400_not_422(_enabled):
    """Missing message scope returns 400 with a clear message."""
    app = create_app()
    client = TestClient(app)
    response = client.post(
        "/api/feedback",
        headers=_auth_headers(),
        json={"rating": "thumbs_up", "trace_id": "t1"},
    )
    assert response.status_code == 400
    assert "message_id" in response.json()["detail"]


@patch("app.routes.feedback.feedback_persistence_enabled", return_value=False)
def test_feedback_requires_supabase(_enabled):
    """Without Supabase persistence configured, return 503."""
    app = create_app()
    client = TestClient(app)
    response = client.post(
        "/api/feedback",
        headers=_auth_headers(),
        json={
            "message_id": str(uuid.uuid4()),
            "conversation_id": str(uuid.uuid4()),
            "rating": "thumbs_up",
        },
    )
    assert response.status_code == 503


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
