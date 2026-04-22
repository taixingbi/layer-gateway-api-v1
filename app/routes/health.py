from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    # Simple liveness signal for load balancers and readiness checks.
    return {"status": "ok"}
