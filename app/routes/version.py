"""Build metadata route (no dependency checks)."""

from fastapi import APIRouter

from app.build_info import version_payload

router = APIRouter(tags=["ops"])


@router.get("/version")
async def version():
    """Return image and git build metadata for ops and dashboards."""
    return version_payload()
