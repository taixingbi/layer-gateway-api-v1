"""Allowlist validation for Supabase password-reset ``redirect_to`` URLs."""

from urllib.parse import urlparse

from fastapi import HTTPException

from app.core.config import Settings
from app.core.logging import log_event

RESET_PASSWORD_PATH = "/auth/reset-password"


def _origin(url: str) -> str:
    """Parse ``scheme://netloc`` from a URL; raise 400 if invalid."""
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid URL")
    return f"{parsed.scheme}://{parsed.netloc}"


def redirect_origin_allowlist(settings: Settings) -> set[str]:
    """Origins allowed for ``redirect_to`` from ``FRONTEND_URL`` and ``ADDITIONAL_FRONTEND_URLS``."""
    origins: set[str] = set()
    for raw in (settings.frontend_url, *settings.additional_frontend_urls.split(",")):
        part = (raw or "").strip()
        if not part:
            continue
        if "://" not in part:
            part = f"http://{part}"
        try:
            origins.add(_origin(part))
        except HTTPException:
            continue
    return origins


def resolve_password_reset_redirect(settings: Settings, override: str | None = None) -> str:
    """Build allowlisted redirect URL for Supabase reset emails and log resolve/reject events."""
    default = f"{settings.frontend_url.rstrip('/')}{RESET_PASSWORD_PATH}"
    if not override or not override.strip():
        log_event(
            "password_reset_redirect_resolved",
            phase="auth",
            redirect_to=default,
            source="default",
            override_provided=False,
        )
        return default

    url = override.strip()
    parsed = urlparse(url)
    if parsed.path.rstrip("/") != RESET_PASSWORD_PATH:
        log_event(
            "password_reset_redirect_rejected",
            level="WARN",
            phase="auth",
            redirect_to=url,
            reason="invalid_path",
            override_provided=True,
        )
        raise HTTPException(
            status_code=400,
            detail=f"redirect_to must be exactly {{origin}}{RESET_PASSWORD_PATH}",
        )

    origin = _origin(url)
    allowed = redirect_origin_allowlist(settings)
    if origin not in allowed:
        log_event(
            "password_reset_redirect_rejected",
            level="WARN",
            phase="auth",
            redirect_to=url,
            reason="origin_not_allowed",
            origin=origin,
            override_provided=True,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"redirect_to origin {origin!r} is not allowed. "
                f"Set FRONTEND_URL (and optional ADDITIONAL_FRONTEND_URLS) on the gateway to include this origin."
            ),
        )
    log_event(
        "password_reset_redirect_resolved",
        phase="auth",
        redirect_to=url,
        source="override",
        override_provided=True,
    )
    return url
