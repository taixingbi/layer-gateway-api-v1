"""Chat API: non-stream JSON and gateway-managed SSE streaming to the orchestrator."""

import json
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import get_settings
from app.core.logging import log_event
from app.schemas.chat_request import ChatRequest
from app.schemas.history import ChatHistoryMessage
from app.schemas.chat_response import ChatResponse, ErrorDetails, Usage
from app.schemas.orchestrator import (
    AuthContext,
    OrchestratorChatRequest,
    OrchestratorClientInfo,
    OrchestratorContext,
    OrchestratorInput,
)
from app.services.orchestrator_call_context import OrchestratorCallContext

def _gateway_sse_chunk_token_text(chunk: str) -> str | None:
    """Parse ``event: token`` / ``data: {...}`` frame produced by ``OrchestratorClient``."""
    for line in chunk.splitlines():
        if line.startswith("data:"):
            raw = line[5:].strip()
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                return raw
            if isinstance(obj, dict):
                return str(obj.get("text") or obj.get("token") or "")
            return str(obj)
    return None


def _upstream_token_text_is_internal_context_error(text: str) -> bool:
    """Detect orchestrator bug where ContextVar misuse is stringified into stream text."""
    if not text:
        return False
    if "ContextVar" in text and "different Context" in text:
        return True
    if "pipeline_phase" in text and "Token" in text and "ContextVar" in text:
        return True
    return False


router = APIRouter(prefix="/api", tags=["chat"])


def _generate_session_id() -> str:
    """Create gateway-owned session IDs when client does not supply one."""
    return f"sess_{uuid4().hex[:12]}"


def _resolve_session_id(request: Request) -> str:
    """Resolve session from ``X-Session-Id`` header or mint a gateway-owned id (never from JSON body)."""
    header_raw = (request.headers.get("x-session-id") or "").strip()
    if header_raw:
        if len(header_raw) < 3 or len(header_raw) > 128:
            raise HTTPException(
                status_code=400,
                detail="X-Session-Id must be between 3 and 128 characters",
            )
        return header_raw
    return _generate_session_id()


def _attach_chat_session_id(request: Request) -> str:
    """Resolve session once, set ``request.state.session_id`` for access logs and early ``log_event`` lines."""
    existing = getattr(request.state, "session_id", None)
    if existing is not None:
        return existing
    sid = _resolve_session_id(request)
    request.state.session_id = sid
    return sid


def _resolve_conversation_id(request: Request, payload: ChatRequest) -> str | None:
    """Prefer ``X-Conversation-Id`` when set (3–128 chars); otherwise JSON ``conversation_id``."""
    header_raw = (request.headers.get("x-conversation-id") or "").strip()
    if header_raw:
        if len(header_raw) < 3 or len(header_raw) > 128:
            raise HTTPException(
                status_code=400,
                detail="X-Conversation-Id must be between 3 and 128 characters",
            )
        return header_raw
    return payload.conversation_id


def _normalize_request(payload: ChatRequest, request: Request) -> ChatRequest:
    """Normalize and validate chat input before downstream orchestration."""
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message cannot be empty")
    # Enforce centralized size guardrails configured at gateway edge.
    max_len = get_settings().chat_message_max_length
    if len(message) > max_len:
        raise HTTPException(status_code=400, detail=f"message exceeds max length ({max_len})")
    # Persist normalized values used by orchestrator payload builder.
    payload.message = message
    payload.metadata = payload.metadata or {}
    if payload.history:
        normalized: list[ChatHistoryMessage] = []
        for turn in payload.history:
            content = turn.content.strip()
            if not content:
                raise HTTPException(status_code=400, detail="history entry content cannot be empty")
            if len(content) > max_len:
                raise HTTPException(
                    status_code=400,
                    detail=f"history entry exceeds max length ({max_len})",
                )
            normalized.append(ChatHistoryMessage(role=turn.role, content=content))
        payload.history = normalized
    return payload


def _build_orchestrator_request(payload: ChatRequest, request: Request) -> OrchestratorChatRequest:
    """Translate frontend payload plus gateway context into orchestrator contract."""
    # Trusted claims come from middleware, not from client body.
    auth_context = request.state.auth_context
    # Correlation IDs are minted/propagated by request-context middleware.
    request_id = request.state.request_id
    trace_id = request.state.trace_id
    session_id = getattr(request.state, "session_id", None)
    if session_id is None:
        session_id = _resolve_session_id(request)
        request.state.session_id = session_id
    return OrchestratorChatRequest(
        auth=AuthContext(**auth_context),
        context=OrchestratorContext(
            session_id=session_id,
            conversation_id=_resolve_conversation_id(request, payload),
            request_id=request_id,
            trace_id=trace_id,
        ),
        input=OrchestratorInput(question=payload.message, history=payload.history),
        client=OrchestratorClientInfo(
            source="nextjs-web",
            page=payload.metadata.get("page"),
            metadata=payload.metadata,
        ),
    )


def _chat_correlation_log_kwargs(request: Request) -> dict[str, Any]:
    """Common correlation fields for chat route ``log_event`` lines (compact when no conversation)."""
    out: dict[str, str] = {
        "request_id": request.state.request_id,
        "trace_id": request.state.trace_id,
        "session_id": request.state.session_id,
    }
    cid = getattr(request.state, "conversation_id", None)
    if cid:
        out["conversation_id"] = cid
    return out


def _build_call_context(
    request: Request, orchestrator_payload: OrchestratorChatRequest, stream: bool
) -> OrchestratorCallContext:
    auth = request.state.auth_context
    roles = tuple(auth.get("roles") or [])
    groups = tuple(auth.get("groups") or [])
    teams = tuple(auth.get("teams") or [])
    return OrchestratorCallContext(
        session_id=orchestrator_payload.context.session_id,
        request_id=orchestrator_payload.context.request_id,
        trace_id=orchestrator_payload.context.trace_id,
        user_id=auth["user_id"],
        roles=roles,
        groups=groups,
        teams=teams,
        stream=stream,
        conversation_id=orchestrator_payload.context.conversation_id,
    )


@router.post("/chat", response_model=ChatResponse, response_model_exclude_none=True)
async def chat(request: Request, payload: ChatRequest):
    """Handle chat (JSON or SSE) via orchestrator with correlation and validation."""
    request.state.access_log_stream = False
    _attach_chat_session_id(request)
    request.state.conversation_id = _resolve_conversation_id(request, payload)
    # Record ingress event with correlation fields before processing.
    log_event(
        "request_received",
        path="/api/chat",
        method=request.method,
        **_chat_correlation_log_kwargs(request),
    )
    payload = _normalize_request(payload, request)
    # Validation checkpoint for request observability.
    log_event(
        "request_validated",
        path="/api/chat",
        method=request.method,
        **_chat_correlation_log_kwargs(request),
    )
    orchestrator_payload = _build_orchestrator_request(payload, request)
    request.state.conversation_id = orchestrator_payload.context.conversation_id

    # Streaming: `Accept: text/event-stream` or JSON `"stream": true` (query flags are not supported).
    accept = (request.headers.get("accept") or "").lower()
    wants_stream = "text/event-stream" in accept or payload.stream is True
    client = request.app.state.orchestrator_client

    if wants_stream:
        request.state.access_log_stream = True
        # Return gateway-managed SSE stream contract.
        return StreamingResponse(
            _stream_response(request, orchestrator_payload),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    try:
        # Non-stream path performs single request/response orchestration call.
        log_event("orchestrator_call_started", **_chat_correlation_log_kwargs(request))
        ctx = _build_call_context(request, orchestrator_payload, stream=False)
        result = await client.chat(orchestrator_payload, ctx)
        log_event("orchestrator_call_succeeded", **_chat_correlation_log_kwargs(request))
        body = ChatResponse(
            status="success",
            session_id=orchestrator_payload.context.session_id,
            request_id=request.state.request_id,
            trace_id=request.state.trace_id,
            answer=result.answer,
            rewrite=getattr(result, "rewrite", None),
            citations=result.citations,
            follow_up_questions=getattr(result, "follow_up_questions", None) or [],
            usage=Usage(**result.usage) if result.usage else Usage(),
        )
        return JSONResponse(content=body.model_dump(mode="json", exclude_none=True))
    except HTTPException as exc:
        # Keep mapped HTTP semantics while logging failure context.
        log_event("orchestrator_call_failed", **_chat_correlation_log_kwargs(request), status=exc.status_code)
        raise
    except Exception as exc:  # pragma: no cover
        # Defensive fallback for unexpected gateway exceptions.
        log_event("orchestrator_call_failed", **_chat_correlation_log_kwargs(request), error=str(exc))
        raise HTTPException(status_code=500, detail="gateway failed") from exc


async def _stream_response(request: Request, orchestrator_payload: OrchestratorChatRequest):
    """Yield gateway SSE events in the stable frontend stream contract."""
    # Emit correlation metadata first so frontend can tag the stream context.
    meta = {
        "request_id": request.state.request_id,
        "trace_id": request.state.trace_id,
        "session_id": orchestrator_payload.context.session_id,
    }
    if orchestrator_payload.context.conversation_id:
        meta["conversation_id"] = orchestrator_payload.context.conversation_id
    yield f"event: meta\ndata: {json.dumps(meta)}\n\n"
    client = request.app.state.orchestrator_client
    ctx = _build_call_context(request, orchestrator_payload, stream=True)
    upstream_first = True
    ttfb_start = time.perf_counter()
    stream_failed = False
    upstream_done_sent = False
    try:
        # Forward normalized token events from orchestrator stream.
        async for token_event in client.stream_chat(orchestrator_payload, ctx):
            if upstream_first:
                request.state.stream_ttfb_ms = (time.perf_counter() - ttfb_start) * 1000
                upstream_first = False
            if token_event.lstrip().startswith("event: done"):
                upstream_done_sent = True
                yield token_event
                continue
            token_text = _gateway_sse_chunk_token_text(token_event)
            if token_text and _upstream_token_text_is_internal_context_error(token_text):
                stream_failed = True
                err = {
                    "status": "error",
                    "error": {
                        "code": "upstream_internal",
                        "message": "Upstream streaming failed (async context). Fix orchestrator ContextVar usage.",
                    },
                }
                yield f"event: error\ndata: {json.dumps(err)}\n\n"
                break
            yield token_event
        if stream_failed:
            yield 'event: done\ndata: {"status":"error"}\n\n'
        elif not upstream_done_sent:
            # Mark stream completion with terminal success event.
            yield "event: done\ndata: {\"status\":\"success\"}\n\n"
    except HTTPException as exc:
        # Convert mapped HTTP failures to stream-safe error envelope.
        payload = {"status": "error", "error": {"code": str(exc.status_code), "message": exc.detail}}
        yield f"event: error\ndata: {json.dumps(payload)}\n\n"
    except Exception:  # pragma: no cover
        # Fallback error for unexpected stream exceptions.
        payload = {"status": "error", "error": ErrorDetails(code="stream_failed", message="stream failed").model_dump()}
        yield f"event: error\ndata: {json.dumps(payload)}\n\n"
