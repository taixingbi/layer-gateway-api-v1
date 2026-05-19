"""Tests for password-reset redirect allowlist resolution."""

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.services.redirect_auth import resolve_password_reset_redirect


@pytest.fixture(autouse=True)
def _capture_redirect_logs(monkeypatch):
    """Record ``log_event`` calls from redirect_auth for assertions."""
    events: list[tuple[str, dict]] = []

    def _log_event(event: str, **fields):
        """Log event."""
        events.append((event, fields))

    monkeypatch.setattr("app.services.redirect_auth.log_event", _log_event)
    yield events


def test_default_reset_redirect(_capture_redirect_logs):
    """Default reset redirect."""
    settings = Settings(frontend_url="http://192.168.86.179:30186")
    assert (
        resolve_password_reset_redirect(settings, None)
        == "http://192.168.86.179:30186/auth/reset-password"
    )
    assert _capture_redirect_logs[-1][0] == "password_reset_redirect_resolved"
    assert _capture_redirect_logs[-1][1]["source"] == "default"


def test_override_when_origin_allowed(_capture_redirect_logs):
    """Override when origin allowed."""
    settings = Settings(frontend_url="http://192.168.86.179:30186")
    url = "http://192.168.86.179:30186/auth/reset-password"
    assert resolve_password_reset_redirect(settings, url) == url
    assert _capture_redirect_logs[-1][0] == "password_reset_redirect_resolved"
    assert _capture_redirect_logs[-1][1]["source"] == "override"


def test_override_rejected_for_unknown_origin(_capture_redirect_logs):
    """Override rejected for unknown origin."""
    settings = Settings(frontend_url="http://localhost:3000")
    with pytest.raises(HTTPException) as exc:
        resolve_password_reset_redirect(
            settings, "http://192.168.86.179:30186/auth/reset-password"
        )
    assert exc.value.status_code == 400
    assert _capture_redirect_logs[-1][0] == "password_reset_redirect_rejected"
    assert _capture_redirect_logs[-1][1]["reason"] == "origin_not_allowed"
