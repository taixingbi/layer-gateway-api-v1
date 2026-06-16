"""Tests for admin guest history endpoint."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.auth_claims import UserClaims


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer token-123"}


@patch("app.deps.supabase_auth.verify_access_token")
@patch("app.routes.admin_guest_history.admin_client_configured")
@patch("app.routes.admin_guest_history.list_guest_chat_events")
def test_admin_guest_history_ok(mock_list, mock_configured, mock_verify):
    """Admin users can list guest history rows."""
    mock_verify.return_value = UserClaims(
        user_id="admin_1",
        email="a@example.com",
        roles=["admin"],
        team="ops",
        group="platform",
        plan="pro",
    )
    mock_configured.return_value = True
    mock_list.return_value = [
        {
            "id": "e1",
            "created_at": "2026-06-01 09:00:00 EDT",
            "auth_guest": True,
            "user_type": "guest",
            "session_id": "sess_1",
            "trace_id": "trace_1",
            "request_id": "req_1",
            "conversation_id": None,
            "prompt": "hello",
            "prompt_chars": 5,
            "route": "rag",
            "answer_preview": "hi",
            "latency_ms": {"total_ms": 42},
            "client_ip": "10.0.0.1",
            "user_agent": "pytest",
        }
    ]

    app = create_app()
    client = TestClient(app)
    res = client.get("/v1/admin/guest-history?limit=10", headers=_auth_headers())
    assert res.status_code == 200
    body = res.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["prompt"] == "hello"
    mock_list.assert_called_once_with(limit=10)


@patch("app.deps.supabase_auth.verify_access_token")
def test_admin_guest_history_forbidden_for_non_admin(mock_verify):
    """Non-admin users are rejected with 403."""
    mock_verify.return_value = UserClaims(
        user_id="user_1",
        email="u@example.com",
        roles=["customer"],
        team="ops",
        group="platform",
        plan="free",
    )
    app = create_app()
    client = TestClient(app)
    res = client.get("/v1/admin/guest-history", headers=_auth_headers())
    assert res.status_code == 403
