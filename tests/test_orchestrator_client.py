import json

import httpx
import pytest
from fastapi import HTTPException

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
async def test_flat_headers_sends_history_when_set():
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/orchestrator/answer",
    )
    captured: dict = {}
    history = [
        ChatHistoryMessage(role="user", content="What is Taixing Bi US visa status?"),
        ChatHistoryMessage(
            role="assistant",
            content="Taixing has H4 EAD and does not need sponsorship.",
        ),
    ]

    async def handler(request: httpx.Request):
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"answer": "ok", "citations": [], "usage": {}})

    payload = OrchestratorChatRequest(
        auth=AuthContext(user_id="u1", tenant_id="t1", roles=["customer"]),
        context=OrchestratorContext(session_id="sess_1", request_id="req_1", trace_id="trace_1"),
        input=OrchestratorInput(
            question="what is Taixing US visa status?",
            history=history,
        ),
        client=OrchestratorClientInfo(source="nextjs-web"),
    )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        await orchestrator.chat(payload, _ctx(stream=False, conversation_id="conv-smoke-1"))

    assert captured["body"] == {
        "question": "what is Taixing US visa status?",
        "stream": False,
        "conversation_id": "conv-smoke-1",
        "history": [
            {"role": "user", "content": "What is Taixing Bi US visa status?"},
            {"role": "assistant", "content": "Taixing has H4 EAD and does not need sponsorship."},
        ],
    }


@pytest.mark.asyncio
async def test_flat_headers_stream_parses_sse_tokens():
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/orchestrator/answer",
    )
    sse = b'data: {"text":"Hello"}\n\ndata: {"token":" world"}\n\n'

    async def handler(request: httpx.Request):
        body = json.loads(request.content.decode())
        if body.get("stream"):
            assert request.headers.get("accept") == "text/event-stream"
            assert body == {"question": "Hello", "stream": True}
            return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(
            200,
            json={"answer": "Hello world", "citations": [], "follow_up_questions": [], "usage": {}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        chunks = [c async for c in orchestrator.stream_chat(_payload(), _ctx(stream=True))]

    token_chunks = [c for c in chunks if "event: token" in c]
    assert len(token_chunks) == 2
    import json as json_mod

    def _token_text(chunk: str) -> str:
        line = [ln for ln in chunk.splitlines() if ln.startswith("data:")][0]
        return json_mod.loads(line[len("data:") :].strip())["text"]

    assert _token_text(token_chunks[0]) == "Hello"
    assert _token_text(token_chunks[1]) == " world"
    assert any(c.lstrip().startswith("event: done") for c in chunks)


@pytest.mark.asyncio
async def test_flat_headers_stream_forwards_upstream_done_event():
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/orchestrator/answer",
    )
    done_payload = json.dumps(
        {
            "status": "success",
            "follow_up_questions": ["Q1?", "Q2?"],
            "citations": [{"cite_id": 1}],
        }
    )
    sse = (
        b'event: token\ndata: {"text":"Hi"}\n\n'
        + f"event: done\ndata: {done_payload}\n\n".encode()
    )

    async def handler(request: httpx.Request):
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        chunks = [c async for c in orchestrator.stream_chat(_payload(), _ctx(stream=True))]

    assert any(c.lstrip().startswith("event: done") for c in chunks)
    done_chunk = next(c for c in chunks if c.lstrip().startswith("event: done"))
    assert "follow_up_questions" in done_chunk
    assert "Q1?" in done_chunk


@pytest.mark.asyncio
async def test_flat_headers_stream_rag_events_aggregate_into_gateway_done():
    """RAG ``/v1/rag/query`` stream: citations / follow_up_questions on separate events; ``done`` is often empty."""
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/v1/rag/query",
    )
    cite = {
        "cite_id": 1,
        "chunk_id": "1607b45e-1c07-5c29-975d-bbf47ef3129c",
        "source": "personal_profile",
        "text": "H4 EAD",
    }
    sse = (
        b'event: meta\ndata: {"request_id":"req-1"}\n\n'
        b'event: answer_delta\ndata: {"text":"Hello "}\n\n'
        b'event: answer_delta\ndata: {"text":"world"}\n\n'
        b"event: citations\ndata: "
        + json.dumps({"items": [cite]}).encode()
        + b"\n\n"
        b"event: follow_up_questions\ndata: "
        + json.dumps({"items": ["Q1?", "Q2?"]}).encode()
        + b"\n\n"
        b"event: done\ndata: {}\n\n"
    )

    async def handler(request: httpx.Request):
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        chunks = [c async for c in orchestrator.stream_chat(_payload(), _ctx(stream=True))]

    token_chunks = [c for c in chunks if "event: token" in c]
    assert len(token_chunks) == 2
    done_chunk = next(c for c in chunks if c.lstrip().startswith("event: done"))
    done_data = json.loads([ln for ln in done_chunk.splitlines() if ln.startswith("data:")][0][5:].strip())
    assert done_data["status"] == "success"
    assert done_data["citations"] == [cite]
    assert done_data["follow_up_questions"] == ["Q1?", "Q2?"]


@pytest.mark.asyncio
async def test_flat_headers_stream_supplements_metadata_when_sse_lacks_citations():
    """Some upstreams stream tokens + empty ``done`` but return citations on non-stream JSON."""
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/v1/rag/query",
    )
    stream_sse = (
        b'event: answer_delta\ndata: {"text":"Answer"}\n\n' b"event: done\ndata: {}\n\n"
    )
    json_body = {
        "answer": "Answer",
        "citations": [{"cite_id": 1, "source": "profile"}],
        "follow_up_questions": ["Follow up?"],
    }

    async def handler(request: httpx.Request):
        body = json.loads(request.content.decode())
        if body.get("stream"):
            return httpx.Response(200, content=stream_sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=json_body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        chunks = [c async for c in orchestrator.stream_chat(_payload(), _ctx(stream=True))]

    done_chunk = next(c for c in chunks if c.lstrip().startswith("event: done"))
    done_data = json.loads([ln for ln in done_chunk.splitlines() if ln.startswith("data:")][0][5:].strip())
    assert done_data["citations"] == [{"cite_id": 1, "source": "profile"}]
    assert done_data["follow_up_questions"] == ["Follow up?"]


@pytest.mark.asyncio
async def test_gateway_json_stream_appends_done_with_metadata_from_non_stream():
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="gateway_json",
        orchestrator_chat_path="/v1/orchestrator/chat",
    )
    stream_lines = b'{"text":"Hello"}\n{"text":" world"}\n'
    json_body = {
        "answer": "Hello world",
        "citations": [{"cite_id": 1}],
        "follow_up_questions": ["Next?"],
        "usage": {},
    }
    calls = {"stream": 0, "json": 0}

    async def handler(request: httpx.Request):
        if request.headers.get("accept") == "text/event-stream":
            calls["stream"] += 1
            return httpx.Response(200, content=stream_lines, headers={"content-type": "application/x-ndjson"})
        calls["json"] += 1
        return httpx.Response(200, json=json_body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        chunks = [c async for c in orchestrator.stream_chat(_payload(), _ctx(stream=True))]

    assert calls["stream"] == 1
    assert calls["json"] == 1
    done_chunk = next(c for c in chunks if c.lstrip().startswith("event: done"))
    done_data = json.loads([ln for ln in done_chunk.splitlines() if ln.startswith("data:")][0][5:].strip())
    assert done_data["citations"] == [{"cite_id": 1}]
    assert done_data["follow_up_questions"] == ["Next?"]
