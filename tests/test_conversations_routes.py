"""Tests for GET /api/conversations and messages."""

import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app


def _auth_headers():
    return {"Authorization": "Bearer token-123"}


@patch("app.deps.supabase_auth.verify_access_token")
@patch("app.routes.conversations.list_conversations")
def test_list_conversations(mock_list, mock_verify):
    """GET /api/conversations returns summaries."""
    from app.services.auth_claims import UserClaims

    mock_verify.return_value = UserClaims(
        user_id="user_001",
        email="u@example.com",
        roles=["user"],
        team="t",
        group="g",
        plan="free",
    )
    cid = str(uuid.uuid4())
    mock_list.return_value = [
        {
            "id": cid,
            "title": "Hello",
            "created_at": "2026-01-01T00:00:00-05:00",
            "updated_at": "2026-01-02T00:00:00-05:00",
        }
    ]

    app = create_app()
    client = TestClient(app)
    response = client.get("/api/conversations", headers=_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert len(data["conversations"]) == 1
    assert data["conversations"][0]["id"] == cid


@patch("app.deps.supabase_auth.verify_access_token")
@patch("app.routes.conversations.list_messages_for_api")
def test_conversation_messages(mock_messages, mock_verify):
    """GET /api/conversations/{id}/messages returns stored turns."""
    from app.services.auth_claims import UserClaims

    mock_verify.return_value = UserClaims(
        user_id="user_001",
        email="u@example.com",
        roles=["user"],
        team="t",
        group="g",
        plan="free",
    )
    cid = str(uuid.uuid4())
    mock_messages.return_value = [
        {"id": 1, "role": "user", "content": "Hi", "created_at": None},
        {"id": 2, "role": "assistant", "content": "Hey", "created_at": None},
    ]

    app = create_app()
    client = TestClient(app)
    response = client.get(
        f"/api/conversations/{cid}/messages",
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["conversation_id"] == cid
    assert len(data["messages"]) == 2
