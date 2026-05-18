import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.services.redirect_auth import resolve_password_reset_redirect


def test_default_reset_redirect():
    settings = Settings(frontend_url="http://192.168.86.179:30186")
    assert (
        resolve_password_reset_redirect(settings, None)
        == "http://192.168.86.179:30186/auth/reset-password"
    )


def test_override_when_origin_allowed():
    settings = Settings(frontend_url="http://192.168.86.179:30186")
    url = "http://192.168.86.179:30186/auth/reset-password"
    assert resolve_password_reset_redirect(settings, url) == url


def test_override_rejected_for_unknown_origin():
    settings = Settings(frontend_url="http://localhost:3000")
    with pytest.raises(HTTPException) as exc:
        resolve_password_reset_redirect(
            settings, "http://192.168.86.179:30186/auth/reset-password"
        )
    assert exc.value.status_code == 400
