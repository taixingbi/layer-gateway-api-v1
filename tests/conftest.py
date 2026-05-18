import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _mock_supabase_middleware_auth(monkeypatch):
    """Protected routes use Supabase verify; mock it so tests need no real project."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
    get_settings.cache_clear()

    def fake_verify(access_token: str, settings=None):
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
    yield
    get_settings.cache_clear()
