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
    normalize_orchestrator_usage,
    orchestrator_payload_dict,
    persistence_enabled,
    _model_from_usage,
    resolve_assistant_model_name,
)
from app.schemas.history import ChatHistoryMessage
from app.schemas.chat_response import ChatResponse, ErrorDetails
from app.schemas.orchestrator import (
    AuthContext,
    OrchestratorChatRequest,
    OrchestratorClientInfo,
    OrchestratorContext,
    OrchestratorInput,
)
from app.services.chat_latency import (
    attach_latency_to_payload,
    chat_latency_recorder,
    orchestrator_workflow_from_source,
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


router = APIRouter(prefix="/v1", tags=["chat"])

SESSION_COOKIE_NAME = "huntai_session_id"
SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _generate_session_id() -> str:
    """Create gateway-owned session IDs when client does not supply one."""
    return f"sess_{uuid4().hex[:12]}"


def _resolve_session_id(request: Request) -> str:
    """Resolve session from header, cookie, or mint (never from JSON body)."""
    header_raw = (request.headers.get("x-session-id") or "").strip()
    if header_raw:
        if len(header_raw) < 3 or len(header_raw) > 128:
            raise HTTPException(
                status_code=400,
                detail="X-Session-Id must be between 3 and 128 characters",
            )
        return header_raw
    cookie_raw = (request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if cookie_raw:
        if len(cookie_raw) < 3 or len(cookie_raw) > 128:
            raise HTTPException(
                status_code=400,
                detail="session cookie must be between 3 and 128 characters",
            )
        return cookie_raw
    sid = _generate_session_id()
    request.state.session_cookie_to_set = sid
    return sid


def _stream_response_headers(request: Request) -> dict[str, str]:
    """SSE response headers including session cookie when the gateway minted a new id."""
    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    sid = getattr(request.state, "session_cookie_to_set", None)
    if sid:
        headers["Set-Cookie"] = (
            f"{SESSION_COOKIE_NAME}={sid}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={SESSION_COOKIE_MAX_AGE}"
        )
    return headers


def _mark_is_new_conversation(request: Request, payload: ChatRequest) -> None:
    """True when this turn starts a new thread (no conversation id from client or header)."""
    header_conv = (request.headers.get("x-conversation-id") or "").strip()
    body_conv = (payload.conversation_id or "").strip() if payload.conversation_id else ""
    request.state.is_new_conversation = not (header_conv or body_conv)


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
            path="/v1/chat",
            reason="supabase_not_configured",
            **_chat_correlation_log_kwargs(request),
        )
        return

    access_token = parse_bearer(request.headers.get("authorization"))
    user_id = request.state.auth_context["user_id"]
    conv_input = getattr(request.state, "conversation_id", None)
    if conv_input is None:
        conv_input = _resolve_conversation_id(request, payload)
    request.state.is_new_conversation = conv_input is None

    try:
        conv_id = ensure_conversation(
            access_token,
            user_id,
            conv_input,
            first_message=payload.message,
        )
        request.state.conversation_id = conv_id
        payload.conversation_id = conv_id
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
            path="/v1/chat",
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
    done_payload: dict[str, Any] | None = None,
    latency_ms: dict[str, Any] | None = None,
) -> str | None:
    """Insert assistant turn after successful orchestrator response; return message id."""
    conv_id = getattr(request.state, "chat_history_conversation_id", None)
    token = getattr(request.state, "chat_history_access_token", None)
    user_id = getattr(request.state, "chat_history_user_id", None)
    if not conv_id or not token or not user_id:
        return None
    text = (answer or "").strip()
    if not text:
        return None
    latency = getattr(request.state, "chat_latency", None)
    t_db = latency.measure() if latency else None
    orch_workflow = orchestrator_workflow_from_source(done_payload)
    done_dict = done_payload if isinstance(done_payload, dict) else {}
    route_from_done = done_dict.get("route")
    route_meta = done_dict.get("route_meta")
    tool_meta = done_dict.get("tool_meta")
    if latency_ms is None and (orch_workflow or latency):
        latency_ms = chat_latency_recorder(request).build(
            request, orchestrator_workflow=orch_workflow
        )
    normalized_usage = normalize_orchestrator_usage(usage) if usage else normalize_orchestrator_usage(
        done_payload
    )
    metadata = assistant_message_metadata(
        rewrite=rewrite,
        citations=citations,
        follow_up_questions=follow_up_questions,
        model=resolve_assistant_model_name(usage=normalized_usage or usage, done_payload=done_payload),
        route=route_from_done if isinstance(route_from_done, str) else None,
        route_meta=route_meta if isinstance(route_meta, dict) else None,
        tool_meta=tool_meta if isinstance(tool_meta, dict) else None,
        usage=normalized_usage or None,
        latency_ms=latency_ms,
    )
    try:
        msg_id = append_message(
            token,
            user_id,
            conv_id,
            "assistant",
            text,
            status=message_persist_status(),
            metadata=metadata,
        )
        if msg_id:
            request.state.last_assistant_message_id = msg_id
        if latency and t_db is not None:
            latency.add_db_write_assistant_message(t_db)
        return msg_id
    except ChatHistoryUnavailable:
        return None


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
    _mark_is_new_conversation(request, payload)
    request.state.conversation_id = _resolve_conversation_id(request, payload)
    # Record ingress event with correlation fields before processing.
    log_event(
        "request_received",
        path="/v1/chat",
        method=request.method,
        **_chat_correlation_log_kwargs(request),
    )
    latency = chat_latency_recorder(request)
    t_validate = latency.measure()
    payload = _normalize_request(payload, request)
    latency.add_request_validation(t_validate)
    # Validation checkpoint for request observability.
    log_event(
        "request_validated",
        path="/v1/chat",
        method=request.method,
        **_chat_correlation_log_kwargs(request),
    )
    t_db_user = latency.measure()
    _prepare_chat_history(request, payload)
    latency.add_db_write_user_message(t_db_user)
    orchestrator_payload = _build_orchestrator_request(payload, request)
    persisted_cid = getattr(request.state, "conversation_id", None)
    if persisted_cid:
        orchestrator_payload.context.conversation_id = persisted_cid
    elif orchestrator_payload.context.conversation_id:
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
            headers=_stream_response_headers(request),
        )

    try:
        # Non-stream path performs single request/response orchestration call.
        log_event("orchestrator_call_started", **_chat_correlation_log_kwargs(request))
        ctx = _build_call_context(request, orchestrator_payload, stream=False)
        t_orch = latency.measure()
        result = await client.chat(orchestrator_payload, ctx)
        latency.add_orchestrator_call(t_orch)
        log_event("orchestrator_call_succeeded", **_chat_correlation_log_kwargs(request))
        orch_workflow = orchestrator_workflow_from_source(result)
        assistant_message_id = _persist_assistant_message(
            request,
            result.answer,
            rewrite=getattr(result, "rewrite", None),
            citations=result.citations,
            follow_up_questions=getattr(result, "follow_up_questions", None) or [],
            usage=normalize_orchestrator_usage(result),
            done_payload=orchestrator_payload_dict(result),
        )
        conv_id = getattr(request.state, "conversation_id", None)
        is_new = getattr(request.state, "is_new_conversation", False)
        orch_raw = orchestrator_payload_dict(result)
        if orch_raw.get("is_new_conversation") is not None:
            is_new = bool(orch_raw["is_new_conversation"])
        else:
            meta = orch_raw.get("meta")
            if isinstance(meta, dict) and meta.get("is_new_conversation") is not None:
                is_new = bool(meta["is_new_conversation"])
        body = ChatResponse(
            status="success",
            session_id=orchestrator_payload.context.session_id,
            request_id=request.state.request_id,
            trace_id=request.state.trace_id,
            conversation_id=conv_id,
            is_new_conversation=is_new,
            assistant_message_id=assistant_message_id,
            answer=result.answer,
            rewrite=getattr(result, "rewrite", None),
            route=getattr(result, "route", None),
            citations=result.citations,
            follow_up_questions=getattr(result, "follow_up_questions", None) or [],
            usage=normalize_orchestrator_usage(result),
            latency_ms=latency.build(request, orchestrator_workflow=orch_workflow),
        )
        json_resp = JSONResponse(content=body.model_dump(mode="json", exclude_none=True))
        sid = getattr(request.state, "session_cookie_to_set", None)
        if sid:
            json_resp.set_cookie(
                SESSION_COOKIE_NAME,
                sid,
                httponly=True,
                samesite="lax",
                max_age=SESSION_COOKIE_MAX_AGE,
                path="/",
            )
        return json_resp
    except HTTPException as exc:
        # Keep mapped HTTP semantics while logging failure context.
        log_event("orchestrator_call_failed", **_chat_correlation_log_kwargs(request), status=exc.status_code)
        raise
    except Exception as exc:  # pragma: no cover
        # Defensive fallback for unexpected gateway exceptions.
        log_event("orchestrator_call_failed", **_chat_correlation_log_kwargs(request), error=str(exc))
        raise HTTPException(status_code=500, detail="gateway failed") from exc


def _format_done_sse_chunk(done_body: dict[str, Any]) -> str:
    """Format one gateway ``done`` SSE event."""
    return f"event: done\ndata: {json.dumps(done_body)}\n\n"


async def _stream_response(request: Request, orchestrator_payload: OrchestratorChatRequest):
    """Yield gateway SSE events in the stable frontend stream contract."""
    # Emit correlation metadata first so frontend can tag the stream context.
    stream_cid = (
        getattr(request.state, "conversation_id", None) or orchestrator_payload.context.conversation_id
    )
    meta = {
        "request_id": request.state.request_id,
        "trace_id": request.state.trace_id,
        "session_id": orchestrator_payload.context.session_id,
        "is_new_conversation": bool(getattr(request.state, "is_new_conversation", False)),
    }
    if stream_cid:
        meta["conversation_id"] = stream_cid
    yield f"event: meta\ndata: {json.dumps(meta)}\n\n"
    stream_assistant_message_id: str | None = None
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
    pending_done_body: dict[str, Any] | None = None
    latency = chat_latency_recorder(request)
    try:
        t_orch = latency.measure()
        # Forward normalized token events from orchestrator stream.
        async for token_event in client.stream_chat(orchestrator_payload, ctx):
            if upstream_first:
                request.state.stream_ttfb_ms = (time.perf_counter() - ttfb_start) * 1000
                upstream_first = False
            if token_event.lstrip().startswith("event: route"):
                yield token_event
                continue
            if token_event.lstrip().startswith("event: done"):
                upstream_done_sent = True
                pending_done_body = _gateway_sse_chunk_done_payload(token_event) or {"status": "success"}
                rewrite_val = pending_done_body.get("rewrite")
                if isinstance(rewrite_val, str) and rewrite_val.strip():
                    stream_rewrite = rewrite_val.strip()
                cites = pending_done_body.get("citations")
                if isinstance(cites, list):
                    stream_citations = [c for c in cites if isinstance(c, dict)]
                follow = pending_done_body.get("follow_up_questions")
                if isinstance(follow, list):
                    stream_follow_ups = [str(q) for q in follow if q]
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
        latency.add_orchestrator_call(t_orch)
        if stream_failed:
            yield 'event: done\ndata: {"status":"error"}\n\n'
        else:
            done_for_model = pending_done_body if isinstance(pending_done_body, dict) else None
            stream_usage = normalize_orchestrator_usage(done_for_model)
            stream_model = resolve_assistant_model_name(
                usage=stream_usage, done_payload=done_for_model
            )
            orch_workflow = orchestrator_workflow_from_source(done_for_model)
            done_body = (
                dict(pending_done_body)
                if isinstance(pending_done_body, dict)
                else {"status": "success"}
            )
            if stream_model and not done_body.get("model"):
                done_body["model"] = stream_model
            if stream_rewrite and not done_body.get("rewrite"):
                done_body["rewrite"] = stream_rewrite
            if stream_citations and not done_body.get("citations"):
                done_body["citations"] = stream_citations
            if stream_follow_ups and not done_body.get("follow_up_questions"):
                done_body["follow_up_questions"] = stream_follow_ups
            stream_assistant_message_id = _persist_assistant_message(
                request,
                "".join(assistant_parts),
                rewrite=stream_rewrite,
                citations=stream_citations,
                follow_up_questions=stream_follow_ups,
                usage=stream_usage,
                done_payload=done_for_model,
            )
            if stream_assistant_message_id:
                done_body["assistant_message_id"] = stream_assistant_message_id
            orch_is_new = done_body.get("is_new_conversation")
            if orch_is_new is None:
                done_meta = done_body.get("meta")
                if isinstance(done_meta, dict) and "is_new_conversation" in done_meta:
                    orch_is_new = done_meta.get("is_new_conversation")
            if orch_is_new is not None:
                done_body["is_new_conversation"] = bool(orch_is_new)
            if stream_assistant_message_id or stream_cid:
                late_meta: dict[str, Any] = {}
                if stream_cid:
                    late_meta["conversation_id"] = stream_cid
                if orch_is_new is not None:
                    late_meta["is_new_conversation"] = bool(orch_is_new)
                if stream_assistant_message_id:
                    late_meta["assistant_message_id"] = stream_assistant_message_id
                if stream_model:
                    late_meta["model"] = stream_model
                yield f"event: meta\ndata: {json.dumps(late_meta)}\n\n"
            attach_latency_to_payload(
                done_body, request, orchestrator_workflow=orch_workflow
            )
            stream_usage_out = normalize_orchestrator_usage(done_body) or stream_usage
            if stream_usage_out:
                done_body["usage"] = stream_usage_out
            yield _format_done_sse_chunk(done_body)
    except HTTPException as exc:
        # Convert mapped HTTP failures to stream-safe error envelope.
        payload = {"status": "error", "error": {"code": str(exc.status_code), "message": exc.detail}}
        yield f"event: error\ndata: {json.dumps(payload)}\n\n"
    except Exception:  # pragma: no cover
        # Fallback error for unexpected stream exceptions.
        payload = {"status": "error", "error": ErrorDetails(code="stream_failed", message="stream failed").model_dump()}
        yield f"event: error\ndata: {json.dumps(payload)}\n\n"
