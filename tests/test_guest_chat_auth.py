"""Guest chat bearer auth (public /chat via shared service token)."""

from unittest.mock import patch

import pytest

from app.core.config import get_settings
from app.services.guest_chat import guest_chat_auth_context, try_guest_chat_auth


def test_try_guest_chat_auth_matches_token(monkeypatch):
    monkeypatch.setenv("GUEST_CHAT_ENABLED", "true")
    monkeypatch.setenv("GUEST_CHAT_SERVICE_TOKEN", "guest-secret")
    get_settings.cache_clear()
    ctx = try_guest_chat_auth("guest-secret", get_settings())
    assert ctx is not None
    assert ctx["roles"] == ["anyuser"]
    assert ctx.get("guest") is True


def test_try_guest_chat_auth_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("GUEST_CHAT_ENABLED", "true")
    monkeypatch.setenv("GUEST_CHAT_SERVICE_TOKEN", "guest-secret")
    get_settings.cache_clear()
    assert try_guest_chat_auth("wrong", get_settings()) is None


def test_try_guest_chat_auth_disabled(monkeypatch):
    monkeypatch.setenv("GUEST_CHAT_ENABLED", "false")
    monkeypatch.setenv("GUEST_CHAT_SERVICE_TOKEN", "guest-secret")
    get_settings.cache_clear()
    assert try_guest_chat_auth("guest-secret", get_settings()) is None


def test_guest_chat_auth_context_roles(monkeypatch):
    monkeypatch.setenv("GUEST_CHAT_USER_ID", "guest")
    get_settings.cache_clear()
    ctx = guest_chat_auth_context(get_settings())
    assert ctx["user_id"] == "guest"
    assert ctx["roles"] == ["anyuser"]
    assert ctx.get("guest") is True


@pytest.fixture
def guest_client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("GUEST_CHAT_ENABLED", "true")
    monkeypatch.setenv("GUEST_CHAT_SERVICE_TOKEN", "guest-secret-token")
    monkeypatch.setenv("GUEST_CHAT_USER_ID", "guest")
    get_settings.cache_clear()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def test_guest_token_accepted_without_supabase_verify(guest_client):
    with patch(
        "app.middleware.auth.verify_access_token_to_auth_context",
        side_effect=AssertionError("must not call Supabase for guest bearer"),
    ):
        res = guest_client.post(
            "/v1/chat",
            headers={"Authorization": "Bearer guest-secret-token"},
            json={"message": "hello", "stream": False},
        )
    assert res.status_code != 401
