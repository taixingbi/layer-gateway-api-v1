from fastapi import APIRouter, HTTPException, Request

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    # Simple liveness signal for load balancers and uptime monitors.
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    """Readiness: orchestrator dependency is reachable (unless probe disabled in settings)."""
    client = getattr(request.app.state, "orchestrator_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="orchestrator client not initialized")

    settings = get_settings()
    if not settings.orchestrator_readiness_probe_enabled:
        return {"status": "ready", "orchestrator": "probe_disabled"}

    ok, detail = await client.readiness_check()
    if not ok:
        raise HTTPException(status_code=503, detail=detail or "orchestrator not ready")
    return {"status": "ready", "orchestrator": "ok"}
