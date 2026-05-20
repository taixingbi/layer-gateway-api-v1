"""Chat API: non-stream JSON and gateway-managed SSE streaming to the orchestrator."""

import json
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import get_settings
from app.core.logging import log_event
from app.deps.auth_bearer import parse_bearer
from app.schemas.chat_request import ChatRequest
from app.services.chat_history_service import (
    ChatHistoryUnavailable,
    append_message,
    assistant_message_metadata,
    ensure_conversation,
    load_messages,
    merge_history,
    message_persist_status,
    persistence_enabled,
    _model_from_usage,
)
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
    event_name = "message"
    data_raw: str | None = None
    for line in chunk.splitlines():
        if line.startswith("event:"):
            event_name = line[6:].strip().lower()
        elif line.startswith("data:"):
            data_raw = line[5:].strip()
    if event_name != "token" or not data_raw:
        return None
    try:
        obj = json.loads(data_raw)
    except json.JSONDecodeError:
        return data_raw
    if isinstance(obj, dict):
        text = obj.get("text") or obj.get("token") or ""
        return str(text) if text else None
    return str(obj)


def _gateway_sse_chunk_event_data(chunk: str) -> tuple[str, str | None]:
    """Return (event name, data payload) from one SSE frame."""
    event_name = "message"
    data_raw: str | None = None
    for line in chunk.splitlines():
        if line.startswith("event:"):
            event_name = line[6:].strip().lower()
        elif line.startswith("data:"):
            data_raw = line[5:].strip()
    return event_name, data_raw


def _gateway_sse_chunk_rewrite_text(chunk: str) -> str | None:
    """Parse ``event: rewrite`` text for assistant message metadata."""
    event_name, data_raw = _gateway_sse_chunk_event_data(chunk)
    if event_name != "rewrite" or not data_raw:
        return None
    try:
        obj = json.loads(data_raw)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict):
        text = obj.get("text")
        return text.strip() if isinstance(text, str) and text.strip() else None
    return None


def _gateway_sse_chunk_done_payload(chunk: str) -> dict[str, Any] | None:
    """Parse ``event: done`` data JSON from a gateway SSE frame."""
    event_name, data_raw = _gateway_sse_chunk_event_data(chunk)
    if event_name != "done" or not data_raw:
        return None
    try:
        parsed = json.loads(data_raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


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


def _prepare_chat_history(request: Request, payload: ChatRequest) -> None:
    """Load DB history, merge client tail, persist user message when Supabase is available."""
    if not persistence_enabled():
        log_event(
            "chat_history_skipped",
            path="/api/chat",
            reason="supabase_not_configured",
            **_chat_correlation_log_kwargs(request),
        )
        return

    access_token = parse_bearer(request.headers.get("authorization"))
    user_id = request.state.auth_context["user_id"]
    conv_input = getattr(request.state, "conversation_id", None)

    try:
        conv_id = ensure_conversation(
            access_token,
            user_id,
            conv_input,
            first_message=payload.message,
        )
        request.state.conversation_id = conv_id
        db_history = load_messages(access_token, user_id, conv_id)
        payload.history = merge_history(db_history, payload.history)
        append_message(
            access_token,
            user_id,
            conv_id,
            "user",
            payload.message,
            status=message_persist_status(),
        )
        request.state.chat_history_access_token = access_token
        request.state.chat_history_user_id = user_id
        request.state.chat_history_conversation_id = conv_id
    except ChatHistoryUnavailable:
        log_event(
            "chat_history_skipped",
            path="/api/chat",
            reason="supabase_unavailable",
            **_chat_correlation_log_kwargs(request),
        )


def _persist_assistant_message(
    request: Request,
    answer: str,
    *,
    rewrite: str | None = None,
    citations: list[dict[str, Any]] | None = None,
    follow_up_questions: list[str] | None = None,
    usage: dict[str, Any] | None = None,
) -> None:
    """Insert assistant turn after successful orchestrator response."""
    conv_id = getattr(request.state, "chat_history_conversation_id", None)
    token = getattr(request.state, "chat_history_access_token", None)
    user_id = getattr(request.state, "chat_history_user_id", None)
    if not conv_id or not token or not user_id:
        return
    text = (answer or "").strip()
    if not text:
        return
    metadata = assistant_message_metadata(
        rewrite=rewrite,
        citations=citations,
        follow_up_questions=follow_up_questions,
        model=_model_from_usage(usage)
        or (get_settings().chat_assistant_model.strip() or None),
    )
    try:
        append_message(
            token,
            user_id,
            conv_id,
            "assistant",
            text,
            status=message_persist_status(),
            metadata=metadata,
        )
    except ChatHistoryUnavailable:
        pass


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
    _prepare_chat_history(request, payload)
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
        _persist_assistant_message(
            request,
            result.answer,
            rewrite=getattr(result, "rewrite", None),
            citations=result.citations,
            follow_up_questions=getattr(result, "follow_up_questions", None) or [],
            usage=result.usage,
        )
        conv_id = getattr(request.state, "conversation_id", None)
        body = ChatResponse(
            status="success",
            session_id=orchestrator_payload.context.session_id,
            request_id=request.state.request_id,
            trace_id=request.state.trace_id,
            conversation_id=conv_id,
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
    assistant_parts: list[str] = []
    stream_rewrite: str | None = None
    stream_citations: list[dict[str, Any]] = []
    stream_follow_ups: list[str] = []
    try:
        # Forward normalized token events from orchestrator stream.
        async for token_event in client.stream_chat(orchestrator_payload, ctx):
            if upstream_first:
                request.state.stream_ttfb_ms = (time.perf_counter() - ttfb_start) * 1000
                upstream_first = False
            if token_event.lstrip().startswith("event: done"):
                upstream_done_sent = True
                done_payload = _gateway_sse_chunk_done_payload(token_event)
                if done_payload:
                    rewrite_val = done_payload.get("rewrite")
                    if isinstance(rewrite_val, str) and rewrite_val.strip():
                        stream_rewrite = rewrite_val.strip()
                    cites = done_payload.get("citations")
                    if isinstance(cites, list):
                        stream_citations = [c for c in cites if isinstance(c, dict)]
                    follow = done_payload.get("follow_up_questions")
                    if isinstance(follow, list):
                        stream_follow_ups = [str(q) for q in follow if q]
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
            if token_text:
                assistant_parts.append(token_text)
            else:
                rewrite_text = _gateway_sse_chunk_rewrite_text(token_event)
                if rewrite_text:
                    stream_rewrite = rewrite_text
            yield token_event
        if stream_failed:
            yield 'event: done\ndata: {"status":"error"}\n\n'
        else:
            if not upstream_done_sent:
                yield "event: done\ndata: {\"status\":\"success\"}\n\n"
            _persist_assistant_message(
                request,
                "".join(assistant_parts),
                rewrite=stream_rewrite,
                citations=stream_citations,
                follow_up_questions=stream_follow_ups,
            )
    except HTTPException as exc:
        # Convert mapped HTTP failures to stream-safe error envelope.
        payload = {"status": "error", "error": {"code": str(exc.status_code), "message": exc.detail}}
        yield f"event: error\ndata: {json.dumps(payload)}\n\n"
    except Exception:  # pragma: no cover
        # Fallback error for unexpected stream exceptions.
        payload = {"status": "error", "error": ErrorDetails(code="stream_failed", message="stream failed").model_dump()}
        yield f"event: error\ndata: {json.dumps(payload)}\n\n"
