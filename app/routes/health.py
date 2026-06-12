"""Liveness and readiness probe routes."""

from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request

from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


def _frontend_url_misconfigured(settings: Settings) -> str | None:
    """Return an error message when prod FRONTEND_URL would break password-reset redirects."""
    if (settings.env or "").strip().lower() != "prod":
        return None
    url = (settings.frontend_url or "").strip().rstrip("/")
    if not url:
        return "FRONTEND_URL is not set"
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1") or url.startswith("http://localhost"):
        return f"FRONTEND_URL must not be localhost in prod (got {url!r})"
    return None


@router.get("/health")
async def health():
    """Return simple liveness for load balancers and uptime monitors."""
    # Simple liveness signal for load balancers and uptime monitors.
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    """Readiness: orchestrator dependency is reachable (unless probe disabled in settings)."""
    client = getattr(request.app.state, "orchestrator_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="orchestrator client not initialized")

    settings = get_settings()
    frontend_err = _frontend_url_misconfigured(settings)
    if frontend_err:
        raise HTTPException(status_code=503, detail=frontend_err)

    if not settings.orchestrator_readiness_probe_enabled:
        return {
            "status": "ready",
            "orchestrator": "probe_disabled",
            "frontend_url": settings.frontend_url.rstrip("/"),
        }

    ok, detail = await client.readiness_check()
    if not ok:
        raise HTTPException(status_code=503, detail=detail or "orchestrator not ready")
    return {
        "status": "ready",
        "orchestrator": "ok",
        "frontend_url": settings.frontend_url.rstrip("/"),
    }
