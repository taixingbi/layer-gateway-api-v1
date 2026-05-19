"""Prometheus scrape endpoint."""

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from fastapi import APIRouter

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics():
    """Expose Prometheus metrics in text format."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
