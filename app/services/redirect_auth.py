from urllib.parse import urlparse

from fastapi import HTTPException

from app.core.config import Settings

RESET_PASSWORD_PATH = "/auth/reset-password"


def _origin(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid URL")
    return f"{parsed.scheme}://{parsed.netloc}"


def redirect_origin_allowlist(settings: Settings) -> set[str]:
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
    """Build allowlisted redirect URL for Supabase reset emails."""
    default = f"{settings.frontend_url.rstrip('/')}{RESET_PASSWORD_PATH}"
    if not override or not override.strip():
        return default

    url = override.strip()
    parsed = urlparse(url)
    if parsed.path.rstrip("/") != RESET_PASSWORD_PATH:
        raise HTTPException(
            status_code=400,
            detail=f"redirect_to must be exactly {{origin}}{RESET_PASSWORD_PATH}",
        )

    origin = _origin(url)
    allowed = redirect_origin_allowlist(settings)
    if origin not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"redirect_to origin {origin!r} is not allowed. "
                f"Set FRONTEND_URL (and optional ADDITIONAL_FRONTEND_URLS) on the gateway to include this origin."
            ),
        )
    return url
