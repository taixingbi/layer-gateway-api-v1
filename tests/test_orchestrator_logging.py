"""Orchestrator client logs match layer_orchestrator ``gateway_meta`` shape."""

import json
from unittest.mock import patch

import httpx
import pytest

from app.core.config import Settings
from app.schemas.history import ChatHistoryMessage
from app.schemas.orchestrator import (
    AuthContext,
    OrchestratorChatRequest,
    OrchestratorClientInfo,
    OrchestratorContext,
    OrchestratorInput,
)
from app.services.orchestrator_call_context import OrchestratorCallContext
from app.services.orchestrator_client import (
    ORCHESTRATOR_API_REQUEST_EVENT,
    ORCHESTRATOR_API_RESPONSE_EVENT,
    ORCHESTRATOR_HTTP_LOGGER,
    ORCHESTRATOR_HTTP_PHASE,
    OrchestratorClient,
)


def _flat_payload() -> OrchestratorChatRequest:
    history = [
        ChatHistoryMessage(role="user", content="What is Taixing Bi US visa status?"),
        ChatHistoryMessage(role="assistant", content="Taixing has H4 EAD."),
    ]
    return OrchestratorChatRequest(
        auth=AuthContext(user_id="u1", tenant_id="t1", roles=["hr"]),
        context=OrchestratorContext(
            session_id="sess_123",
            request_id="req_demo_001",
            trace_id="trace_demo_001",
            conversation_id="conv_456",
        ),
        input=OrchestratorInput(
            question="what is Taixing US visa status?",
            history=history,
        ),
        client=OrchestratorClientInfo(source="nextjs-web"),
    )


def _ctx() -> OrchestratorCallContext:
    return OrchestratorCallContext(
        session_id="sess_123",
        request_id="req_demo_001",
        trace_id="trace_demo_001",
        user_id="taixing",
        roles=("hr",),
        groups=("engineering",),
        teams=("rag-platform",),
        stream=False,
        conversation_id="conv_456",
    )


@pytest.mark.asyncio
async def test_orchestrator_api_logs_use_gateway_meta_shape():
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/orchestrator/answer",
        orchestrator_base_url="http://orchestrator:8080",
    )
    captured: list[dict] = []

    def capture_log(event: str, **fields):
        captured.append({"event": event, **fields})

    async def handler(request: httpx.Request):
        body = json.loads(request.content.decode())
        assert "history" in body
        return httpx.Response(
            200,
            json={
                "answer": "H4 EAD.",
                "citations": [],
                "follow_up_questions": ["Renewal?"],
            },
            headers={"content-type": "application/json"},
        )

    transport = httpx.MockTransport(handler)
    with patch("app.services.orchestrator_client.log_event", side_effect=capture_log):
        async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
            orchestrator = OrchestratorClient(client=client, settings=settings)
            await orchestrator.chat(_flat_payload(), _ctx())

    assert len(captured) == 2
    req_log, res_log = captured
    assert req_log["event"] == ORCHESTRATOR_API_REQUEST_EVENT
    assert req_log["logger"] == ORCHESTRATOR_HTTP_LOGGER
    assert req_log["phase"] == ORCHESTRATOR_HTTP_PHASE
    assert req_log["message"] == ORCHESTRATOR_API_REQUEST_EVENT
    assert req_log["status"] == "-"
    assert req_log["request_id"] == "req_demo_001"
    assert req_log["conversation_id"] == "conv_456"
    assert req_log["omit_service"] is True

    req_meta = req_log["gateway_meta"]
    assert req_meta["url"] == "http://orchestrator:8080/orchestrator/answer"
    assert req_meta["orchestrator_api_request"]["question"] == "what is Taixing US visa status?"
    assert len(req_meta["orchestrator_api_request"]["history"]) == 2
    assert req_meta["orchestrator_api_request_headers"]["X-User-Id"] == "taixing"

    assert res_log["event"] == ORCHESTRATOR_API_RESPONSE_EVENT
    res_meta = res_log["gateway_meta"]
    assert res_meta["http_status_code"] == 200
    assert res_meta["content_type"] == "application/json"
    assert res_meta["orchestrator_api_response"]["answer"] == "H4 EAD."
