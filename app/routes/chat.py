import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.core.logging import log_event
from app.schemas.chat_request import ChatRequest
from app.schemas.chat_response import ChatResponse, ErrorDetails, Usage
from app.schemas.orchestrator import (
    AuthContext,
    OrchestratorChatRequest,
    OrchestratorClientInfo,
    OrchestratorContext,
    OrchestratorInput,
)

router = APIRouter(prefix="/api", tags=["chat"])


def _generate_session_id() -> str:
    """Create gateway-owned session IDs when client does not supply one."""
    return f"sess_{uuid4().hex[:12]}"


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
    return payload


def _build_orchestrator_request(payload: ChatRequest, request: Request) -> OrchestratorChatRequest:
    """Translate frontend payload plus gateway context into orchestrator contract."""
    # Trusted claims come from middleware, not from client body.
    auth_context = request.state.auth_context
    # Correlation IDs are minted/propagated by request-context middleware.
    request_id = request.state.request_id
    trace_id = request.state.trace_id
    # Session continuity is client-provided when valid, otherwise gateway-owned.
    session_id = payload.session_id or _generate_session_id()
    request.state.session_id = session_id
    return OrchestratorChatRequest(
        auth=AuthContext(**auth_context),
        context=OrchestratorContext(
            session_id=session_id,
            conversation_id=payload.conversation_id,
            request_id=request_id,
            trace_id=trace_id,
        ),
        input=OrchestratorInput(question=payload.message),
        client=OrchestratorClientInfo(
            source="nextjs-web",
            page=payload.metadata.get("page"),
            metadata=payload.metadata,
        ),
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, payload: ChatRequest):
    # Record ingress event with correlation fields before processing.
    log_event("request_received", path="/api/chat", request_id=request.state.request_id, trace_id=request.state.trace_id)
    payload = _normalize_request(payload, request)
    # Validation checkpoint for request observability.
    log_event("request_validated", request_id=request.state.request_id, trace_id=request.state.trace_id)
    orchestrator_payload = _build_orchestrator_request(payload, request)

    # Streaming can be requested via Accept header or explicit query flag.
    wants_stream = "text/event-stream" in request.headers.get("accept", "") or request.query_params.get("stream") == "true"
    client = request.app.state.orchestrator_client

    if wants_stream:
        # Return gateway-managed SSE stream contract.
        return StreamingResponse(
            _stream_response(request, orchestrator_payload),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    try:
        # Non-stream path performs single request/response orchestration call.
        log_event("orchestrator_call_started", request_id=request.state.request_id, trace_id=request.state.trace_id)
        result = await client.chat(orchestrator_payload)
        log_event("orchestrator_call_succeeded", request_id=request.state.request_id, trace_id=request.state.trace_id)
        return ChatResponse(
            status="success",
            session_id=orchestrator_payload.context.session_id,
            request_id=request.state.request_id,
            trace_id=request.state.trace_id,
            answer=result.answer,
            citations=result.citations,
            usage=Usage(**result.usage) if result.usage else Usage(),
            error=None,
        )
    except HTTPException as exc:
        # Keep mapped HTTP semantics while logging failure context.
        log_event("orchestrator_call_failed", request_id=request.state.request_id, trace_id=request.state.trace_id, status_code=exc.status_code)
        raise
    except Exception as exc:  # pragma: no cover
        # Defensive fallback for unexpected gateway exceptions.
        log_event("orchestrator_call_failed", request_id=request.state.request_id, trace_id=request.state.trace_id, error=str(exc))
        raise HTTPException(status_code=500, detail="gateway failed") from exc


async def _stream_response(request: Request, orchestrator_payload: OrchestratorChatRequest):
    """Yield gateway SSE events in the stable frontend stream contract."""
    # Emit correlation metadata first so frontend can tag the stream context.
    meta = {
        "request_id": request.state.request_id,
        "trace_id": request.state.trace_id,
        "session_id": orchestrator_payload.context.session_id,
    }
    yield f"event: meta\ndata: {json.dumps(meta)}\n\n"
    client = request.app.state.orchestrator_client
    try:
        # Forward normalized token events from orchestrator stream.
        async for token_event in client.stream_chat(orchestrator_payload):
            yield token_event
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
