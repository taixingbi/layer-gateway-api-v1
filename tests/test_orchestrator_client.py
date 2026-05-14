import json

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
from app.services.orchestrator_call_context import OrchestratorCallContext
from app.services.orchestrator_client import OrchestratorClient


def _payload() -> OrchestratorChatRequest:
    return OrchestratorChatRequest(
        auth=AuthContext(user_id="u1", tenant_id="t1", roles=["customer"]),
        context=OrchestratorContext(session_id="sess_1", request_id="req_1", trace_id="trace_1"),
        input=OrchestratorInput(question="Hello"),
        client=OrchestratorClientInfo(source="nextjs-web"),
    )


def _ctx(stream: bool = False, conversation_id: str | None = None) -> OrchestratorCallContext:
    return OrchestratorCallContext(
        session_id="sess_1",
        request_id="req_1",
        trace_id="trace_1",
        user_id="u1",
        roles=("hr",),
        groups=("engineering",),
        teams=("rag-platform",),
        stream=stream,
        conversation_id=conversation_id,
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
            await orchestrator.chat(_payload(), _ctx())
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
            await orchestrator.chat(_payload(), _ctx())
        assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_flat_headers_chat_posts_headers_and_flat_json():
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/orchestrator/answer",
    )
    captured: dict = {}

    async def handler(request: httpx.Request):
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())
        assert request.url.path == "/orchestrator/answer"
        return httpx.Response(200, json={"answer": "ok", "citations": [], "usage": {}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        result = await orchestrator.chat(_payload(), _ctx(stream=False))

    assert result.answer == "ok"
    h = captured["headers"]
    assert h.get("x-session-id") == "sess_1"
    assert h.get("x-request-id") == "req_1"
    assert h.get("x-trace-id") == "trace_1"
    assert h.get("x-user-id") == "u1"
    assert h.get("x-user-roles") == "hr"
    assert h.get("x-user-groups") == "engineering"
    assert h.get("x-user-teams") == "rag-platform"
    assert captured["body"] == {"question": "Hello", "stream": False}


@pytest.mark.asyncio
async def test_flat_headers_sends_conversation_id_when_set():
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/orchestrator/answer",
    )
    captured: dict = {}

    async def handler(request: httpx.Request):
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"answer": "ok", "citations": [], "usage": {}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        await orchestrator.chat(_payload(), _ctx(stream=False, conversation_id="conv-99"))

    assert captured["headers"].get("x-conversation-id") == "conv-99"
    assert captured["body"] == {"question": "Hello", "stream": False, "conversation_id": "conv-99"}


@pytest.mark.asyncio
async def test_flat_headers_stream_parses_sse_tokens():
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/orchestrator/answer",
    )
    sse = b'data: {"text":"Hello"}\n\ndata: {"token":" world"}\n\n'

    async def handler(request: httpx.Request):
        assert request.headers.get("accept") == "text/event-stream"
        body = json.loads(request.content.decode())
        assert body == {"question": "Hello", "stream": True}
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        chunks = [c async for c in orchestrator.stream_chat(_payload(), _ctx(stream=True))]

    assert len(chunks) == 2
    import json as json_mod

    def _token_text(chunk: str) -> str:
        line = [ln for ln in chunk.splitlines() if ln.startswith("data:")][0]
        return json_mod.loads(line[len("data:") :].strip())["text"]

    assert _token_text(chunks[0]) == "Hello"
    assert _token_text(chunks[1]) == " world"
