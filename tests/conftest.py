"""Pytest fixtures: test env defaults and mock Supabase auth for protected routes."""

import os

# Set before any ``app`` import so ``Settings()`` validation passes during collection.
os.environ.setdefault("AUTH_MODE", "jwt")
os.environ.setdefault("AUTH_JWT_ISSUER", "test-issuer")
os.environ.setdefault("AUTH_JWT_AUDIENCE", "test-audience")
os.environ.setdefault("AUTH_JWT_JWKS_URL", "http://localhost/.well-known/jwks.json")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")

import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _mock_supabase_middleware_auth(monkeypatch):
    """Protected routes use Supabase verify; mock it so tests need no real project."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
    get_settings.cache_clear()

    def fake_verify(access_token: str, settings=None):
        """Return a fixed auth context for any bearer token in tests."""
        return {
            "user_id": "user_001",
            "tenant_id": "tenant_01",
            "roles": ["customer"],
            "groups": ["engineering"],
            "teams": ["rag-platform"],
        }

    monkeypatch.setattr(
        "app.middleware.auth.verify_access_token_to_auth_context",
        fake_verify,
    )
    monkeypatch.setattr("app.routes.chat.persistence_enabled", lambda: False)
    yield
    get_settings.cache_clear()
