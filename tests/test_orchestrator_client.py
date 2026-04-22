import httpx
import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.schemas.orchestrator import (
    AuthContext,
    OrchestratorChatRequest,
    OrchestratorClientInfo,
    OrchestratorContext,
    OrchestratorInput,
)
from app.services.orchestrator_client import OrchestratorClient


def _payload() -> OrchestratorChatRequest:
    return OrchestratorChatRequest(
        auth=AuthContext(user_id="u1", tenant_id="t1", roles=["customer"]),
        context=OrchestratorContext(session_id="sess_1", request_id="req_1", trace_id="trace_1"),
        input=OrchestratorInput(question="Hello"),
        client=OrchestratorClientInfo(source="nextjs-web"),
    )


@pytest.mark.asyncio
async def test_timeout_maps_to_504():
    settings = Settings(orchestrator_retry_max_attempts=1)

    async def handler(request):
        raise httpx.TimeoutException("timeout")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        with pytest.raises(HTTPException) as exc:
            await orchestrator.chat(_payload())
        assert exc.value.status_code == 504


@pytest.mark.asyncio
async def test_server_error_maps_to_502():
    settings = Settings(orchestrator_retry_max_attempts=1)

    async def handler(request):
        return httpx.Response(500, json={"error": "failed"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        with pytest.raises(HTTPException) as exc:
            await orchestrator.chat(_payload())
        assert exc.value.status_code == 502
