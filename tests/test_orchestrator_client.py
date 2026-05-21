"""OrchestratorClient HTTP mapping, retries, and flat_headers contract."""

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
from app.services.orchestrator_client import OrchestratorClient, _gateway_done_payload


def test_gateway_done_payload_includes_latency_ms():
    raw = json.dumps(
        {
            "status": "ok",
            "latency_ms": {"total": 3632.46, "rag": {"total": 2367.0}},
            "citations": [],
            "follow_up_questions": [],
        }
    )
    body = _gateway_done_payload(raw, citations=[], follow_up_questions=[])
    assert body["latency_ms"]["total"] == 3632.46


def _payload() -> OrchestratorChatRequest:
    """Minimal gateway_json chat payload for tests."""
    return OrchestratorChatRequest(
        auth=AuthContext(user_id="u1", tenant_id="t1", roles=["customer"]),
        context=OrchestratorContext(session_id="sess_1", request_id="req_1", trace_id="trace_1"),
        input=OrchestratorInput(question="Hello"),
        client=OrchestratorClientInfo(source="nextjs-web"),
    )


def _ctx(stream: bool = False, conversation_id: str | None = None) -> OrchestratorCallContext:
    """Default call context with optional stream and conversation id."""
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
    """Timeout maps to 504."""
    settings = Settings(orchestrator_retry_max_attempts=1)

    async def handler(request):
        """Handler."""
        raise httpx.TimeoutException("timeout")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        with pytest.raises(HTTPException) as exc:
            await orchestrator.chat(_payload(), _ctx())
        assert exc.value.status_code == 504


@pytest.mark.asyncio
async def test_server_error_maps_to_502():
    """Server error maps to 502."""
    settings = Settings(orchestrator_retry_max_attempts=1)

    async def handler(request):
        """Handler."""
        return httpx.Response(500, json={"error": "failed"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        with pytest.raises(HTTPException) as exc:
            await orchestrator.chat(_payload(), _ctx())
        assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_flat_headers_chat_posts_headers_and_flat_json():
    """Flat headers chat posts headers and flat json."""
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/orchestrator/answer",
    )
    captured: dict = {}

    async def handler(request: httpx.Request):
        """Handler."""
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
    """Flat headers sends conversation id when set."""
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/orchestrator/answer",
    )
    captured: dict = {}

    async def handler(request: httpx.Request):
        """Handler."""
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
    """Flat headers sends history when set."""
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
        """Handler."""
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
    """Flat headers stream parses sse tokens."""
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/orchestrator/answer",
    )
    sse = b'data: {"text":"Hello"}\n\ndata: {"token":" world"}\n\n'

    async def handler(request: httpx.Request):
        """Handler."""
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
        """Token text."""
        line = [ln for ln in chunk.splitlines() if ln.startswith("data:")][0]
        return json_mod.loads(line[len("data:") :].strip())["text"]

    assert _token_text(token_chunks[0]) == "Hello"
    assert _token_text(token_chunks[1]) == " world"
    assert any(c.lstrip().startswith("event: done") for c in chunks)


@pytest.mark.asyncio
async def test_flat_headers_stream_forwards_upstream_done_event():
    """Flat headers stream forwards upstream done event."""
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
        """Handler."""
        body = json.loads(request.content.decode())
        if body.get("stream"):
            return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(
            200,
            json={
                "answer": "Hi",
                "citations": [{"cite_id": 1}],
                "follow_up_questions": ["Q1?", "Q2?"],
                "latency_ms": {"total": 100.0},
            },
        )

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
        """Handler."""
        body = json.loads(request.content.decode())
        if body.get("stream"):
            return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(
            200,
            json={
                "answer": "Hello world",
                "citations": [cite],
                "follow_up_questions": ["Q1?", "Q2?"],
                "latency_ms": {"total": 250.0},
            },
        )

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
    assert done_data["latency_ms"]["total"] == 250.0


@pytest.mark.asyncio
async def test_flat_headers_stream_orchestrator_rewrite_not_emitted_as_token():
    """Orchestrator NDJSON ``type: rewrite`` must map to gateway ``rewrite`` event, not answer tokens."""
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/orchestrator/answer",
    )
    sse = (
        b'data: {"type":"rewrite","text":"What is the candidate\'s visa status?"}\n\n'
        b'data: {"type":"route","route":"rag"}\n\n'
        b'data: {"type":"answer","text":"H4 EAD."}\n\n'
        b'data: {"type":"done","request_id":"req-1"}\n\n'
    )

    json_body = {
        "answer": "H4 EAD.",
        "rewrite": "What is the candidate's visa status?",
        "citations": [],
        "follow_up_questions": [],
        "usage": {},
    }

    async def handler(request: httpx.Request):
        """Handler."""
        body = json.loads(request.content.decode())
        if body.get("stream"):
            return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=json_body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        chunks = [c async for c in orchestrator.stream_chat(_payload(), _ctx(stream=True))]

    rewrite_chunks = [c for c in chunks if "event: rewrite" in c]
    token_chunks = [c for c in chunks if "event: token" in c]
    assert len(rewrite_chunks) == 1
    assert "candidate" in rewrite_chunks[0]
    assert len(token_chunks) == 1
    assert "H4 EAD" in token_chunks[0]
    assert "candidate" not in token_chunks[0]
    done_chunk = next(c for c in chunks if c.lstrip().startswith("event: done"))
    done_data = json.loads([ln for ln in done_chunk.splitlines() if ln.startswith("data:")][0][5:].strip())
    assert done_data["rewrite"] == "What is the candidate's visa status?"


@pytest.mark.asyncio
async def test_chat_flat_non_stream_passes_rewrite():
    """Chat flat non stream passes rewrite."""
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/orchestrator/answer",
    )

    async def handler(request: httpx.Request):
        """Handler."""
        return httpx.Response(
            200,
            json={
                "answer": "H4 EAD.",
                "rewrite": "What is the candidate's visa status?",
                "citations": [],
                "follow_up_questions": [],
                "usage": {},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://test", transport=transport) as client:
        orchestrator = OrchestratorClient(client=client, settings=settings)
        result = await orchestrator.chat(_payload(), _ctx(stream=False))

    assert result.rewrite == "What is the candidate's visa status?"


@pytest.mark.asyncio
async def test_flat_headers_stream_supplements_timings_when_sse_done_has_citations_only():
    """Stream may include citations on ``done`` but omit ``latency_ms``; gateway fills from non-stream JSON."""
    settings = Settings(
        orchestrator_retry_max_attempts=1,
        orchestrator_contract="flat_headers",
        orchestrator_chat_path="/orchestrator/answer",
    )
    cite = {"cite_id": 1, "source": "personal_profile"}
    stream_sse = (
        b'event: rewrite\ndata: {"text":"visa?"}\n\n'
        b'event: token\ndata: {"text":"H4 EAD."}\n\n'
        + f"event: done\ndata: {json.dumps({'status': 'success', 'citations': [cite], 'follow_up_questions': ['Q?']})}\n\n".encode()
    )
    json_body = {
        "answer": "H4 EAD.",
        "rewrite": "visa?",
        "citations": [cite],
        "follow_up_questions": ["Q?"],
        "latency_ms": {"total": 3632.46, "rag": {"total": 2367.0}},
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
    assert done_data["citations"] == [cite]
    assert done_data["latency_ms"]["total"] == 3632.46


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
        "latency_ms": {"total": 500.0},
    }

    async def handler(request: httpx.Request):
        """Handler."""
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
    assert done_data["latency_ms"]["total"] == 500.0


@pytest.mark.asyncio
async def test_gateway_json_stream_appends_done_with_metadata_from_non_stream():
    """Gateway json stream appends done with metadata from non stream."""
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
        "latency_ms": {"total": 1200.0},
    }
    calls = {"stream": 0, "json": 0}

    async def handler(request: httpx.Request):
        """Handler."""
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
    assert done_data["latency_ms"]["total"] == 1200.0
