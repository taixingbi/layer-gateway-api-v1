"""HTTP client for layer orchestrator chat, stream, feedback, and readiness."""

import json
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.config import Settings
from app.core.logging import log_event
from app.core.time_util import eastern_from_timestamp
from app.schemas.orchestrator import OrchestratorChatRequest, OrchestratorChatResponse
from app.services.chat_history_service import normalize_orchestrator_usage
from app.services.chat_latency import orchestrator_workflow_from_source
from app.services.orchestrator_call_context import OrchestratorCallContext
from app.services.orchestrator_normalize import (
    gateway_done_fields_from_normalized,
    normalize_orchestrator_payload,
)


def _upstream_has_workflow_timings(payload: dict[str, Any]) -> bool:
    """True when payload already carries orchestrator workflow timings."""
    return orchestrator_workflow_from_source(payload) is not None


def _upstream_has_usage(payload: dict[str, Any]) -> bool:
    """True when payload already carries orchestrator usage."""
    return bool(normalize_orchestrator_usage(payload))

_ORCH_LOG_BODY_MAX_CHARS = 8000
ORCHESTRATOR_HTTP_LOGGER = "layer_gateway.orchestrator_http"
ORCHESTRATOR_HTTP_PHASE = "orchestrator_upstream"
ORCHESTRATOR_API_REQUEST_EVENT = "orchestrator_api_request"
ORCHESTRATOR_API_RESPONSE_EVENT = "orchestrator_api_response"


def _orch_correlation_fields(ctx: OrchestratorCallContext | None) -> dict[str, str]:
    """Extract correlation ids from call context for structured logs."""
    if ctx is None:
        return {}
    out: dict[str, str] = {
        "request_id": ctx.request_id,
        "trace_id": ctx.trace_id,
        "session_id": ctx.session_id,
    }
    if ctx.conversation_id:
        out["conversation_id"] = ctx.conversation_id
    return out


def _payload_for_log(payload: Any) -> Any:
    """JSON-serialize for logs; truncate very large bodies."""
    if payload is None:
        return None
    try:
        text = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    if len(text) <= _ORCH_LOG_BODY_MAX_CHARS:
        return payload
    return {"_truncated": True, "preview": text[:_ORCH_LOG_BODY_MAX_CHARS]}


def _response_body_for_log(response: httpx.Response) -> Any:
    """Parse response body for logging with truncation."""
    ct = (response.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        try:
            return _payload_for_log(response.json())
        except json.JSONDecodeError:
            pass
    text = response.text
    if len(text) <= _ORCH_LOG_BODY_MAX_CHARS:
        return text
    return {"_truncated": True, "preview": text[:_ORCH_LOG_BODY_MAX_CHARS]}


def _orchestrator_url(settings: Settings, path: str) -> str:
    """Build absolute orchestrator URL for log metadata."""
    return f"{settings.orchestrator_base_url.rstrip('/')}{path}"


def _orchestrator_log_ts() -> str:
    """Current Eastern ISO timestamp with microsecond precision."""
    return eastern_from_timestamp(time.time(), timespec="microseconds")


def _log_orchestrator_request(
    *,
    settings: Settings,
    method: str,
    path: str,
    ctx: OrchestratorCallContext | None,
    stream: bool,
    headers: dict[str, str] | None = None,
    body: Any = None,
    attempt: int | None = None,
    note: str | None = None,
) -> None:
    """Emit structured ``orchestrator_api_request`` log line."""
    gateway_meta: dict[str, Any] = {
        "url": _orchestrator_url(settings, path),
        "orchestrator_contract": settings.orchestrator_contract,
    }
    if body is not None:
        logged_body = _payload_for_log(body)
        gateway_meta["orchestrator_api_request"] = logged_body
        if isinstance(logged_body, dict):
            if logged_body.get("stream") is False:
                gateway_meta["orchestrator_stream_field"] = "false"
            elif "stream" not in logged_body:
                gateway_meta["orchestrator_stream_field"] = "omitted_default_true"
    if headers:
        gateway_meta["orchestrator_api_request_headers"] = dict(headers)
    if stream:
        gateway_meta["stream"] = True
    if attempt is not None:
        gateway_meta["orchestrator_http_attempt"] = attempt
    if note:
        gateway_meta["note"] = note

    log_event(
        ORCHESTRATOR_API_REQUEST_EVENT,
        logger=ORCHESTRATOR_HTTP_LOGGER,
        phase=ORCHESTRATOR_HTTP_PHASE,
        message=ORCHESTRATOR_API_REQUEST_EVENT,
        method=method,
        path=path,
        status="-",
        gateway_meta=gateway_meta,
        ts=_orchestrator_log_ts(),
        omit_service=True,
        **_orch_correlation_fields(ctx),
    )


def _log_orchestrator_response(
    *,
    settings: Settings,
    method: str,
    path: str,
    ctx: OrchestratorCallContext | None,
    stream: bool,
    status_code: int,
    body: Any = None,
    content_type: str | None = None,
    attempt: int | None = None,
    note: str | None = None,
) -> None:
    """Emit structured ``orchestrator_api_response`` log line."""
    gateway_meta: dict[str, Any] = {
        "url": _orchestrator_url(settings, path),
        "http_status_code": status_code,
        "orchestrator_contract": settings.orchestrator_contract,
    }
    if attempt is not None:
        gateway_meta["orchestrator_http_attempts"] = attempt
    if content_type:
        gateway_meta["content_type"] = content_type
    if stream:
        gateway_meta["stream"] = True
    if body is not None:
        if isinstance(body, str):
            gateway_meta["orchestrator_api_response"] = (
                body
                if len(body) <= _ORCH_LOG_BODY_MAX_CHARS
                else {"_truncated": True, "preview": body[:_ORCH_LOG_BODY_MAX_CHARS]}
            )
        else:
            gateway_meta["orchestrator_api_response"] = _payload_for_log(body)
    if note:
        gateway_meta["note"] = note

    log_event(
        ORCHESTRATOR_API_RESPONSE_EVENT,
        logger=ORCHESTRATOR_HTTP_LOGGER,
        phase=ORCHESTRATOR_HTTP_PHASE,
        message=ORCHESTRATOR_API_RESPONSE_EVENT,
        method=method,
        path=path,
        status="-",
        gateway_meta=gateway_meta,
        ts=_orchestrator_log_ts(),
        omit_service=True,
        **_orch_correlation_fields(ctx),
    )


class OrchestratorClient:
    """Transport adapter responsible for orchestrator chat calls."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        """Bind shared httpx client and gateway settings."""
        self._client = client
        self._settings = settings

    def _flat_headers(self, ctx: OrchestratorCallContext) -> dict[str, str]:
        """Build X-* header map for flat_headers orchestrator contract."""
        h: dict[str, str] = {
            "X-Session-Id": ctx.session_id,
            "X-Request-Id": ctx.request_id,
            "X-Trace-Id": ctx.trace_id,
            "X-User-Id": ctx.user_id,
            "X-User-Roles": ",".join(ctx.roles),
            "X-User-Groups": ",".join(ctx.groups),
            "X-User-Teams": ",".join(ctx.teams),
        }
        if ctx.conversation_id:
            h["X-Conversation-Id"] = ctx.conversation_id
        return h

    def _flat_json_body(self, payload: OrchestratorChatRequest, ctx: OrchestratorCallContext) -> dict[str, Any]:
        """Build minimal JSON body for flat_headers chat POST.

        Orchestrator defaults ``stream`` to true; only send ``"stream": false`` for non-stream.
        """
        body: dict[str, Any] = {"question": payload.input.question}
        if not ctx.stream:
            body["stream"] = False
        if ctx.conversation_id:
            body["conversation_id"] = ctx.conversation_id
        if payload.input.history:
            body["history"] = [turn.model_dump() for turn in payload.input.history]
        return body

    async def chat(self, payload: OrchestratorChatRequest, ctx: OrchestratorCallContext) -> OrchestratorChatResponse:
        """Send non-stream chat requests with bounded retries and mapped errors."""
        if self._settings.orchestrator_contract == "flat_headers":
            return await self._chat_flat(payload, ctx)
        return await self._chat_gateway_json(payload, ctx)

    async def _chat_gateway_json(
        self, payload: OrchestratorChatRequest, ctx: OrchestratorCallContext | None = None
    ) -> OrchestratorChatResponse:
        """POST full gateway_json body with retries and mapped HTTP errors."""
        path = self._settings.orchestrator_chat_path
        body = payload.model_dump()
        last_error: Exception | None = None
        for attempt in range(1, self._settings.orchestrator_retry_max_attempts + 1):
            try:
                _log_orchestrator_request(
                    settings=self._settings,
                    method="POST",
                    path=path,
                    ctx=ctx,
                    stream=False,
                    body=body,
                    attempt=attempt,
                )
                response = await self._client.post(path, json=body)
                if response.status_code == status.HTTP_400_BAD_REQUEST:
                    _log_orchestrator_response(
                        settings=self._settings,
                        method="POST",
                        path=path,
                        ctx=ctx,
                        stream=False,
                        status_code=response.status_code,
                        body=_response_body_for_log(response),
                        content_type=response.headers.get("content-type"),
                        attempt=attempt,
                    )
                    raise HTTPException(status_code=400, detail="Invalid request for orchestrator")
                if response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
                    _log_orchestrator_response(
                        settings=self._settings,
                        method="POST",
                        path=path,
                        ctx=ctx,
                        stream=False,
                        status_code=response.status_code,
                        body=_response_body_for_log(response),
                        content_type=response.headers.get("content-type"),
                        attempt=attempt,
                    )
                    raise HTTPException(status_code=response.status_code, detail="Upstream auth failure")
                if response.status_code >= 500:
                    _log_orchestrator_response(
                        settings=self._settings,
                        method="POST",
                        path=path,
                        ctx=ctx,
                        stream=False,
                        status_code=response.status_code,
                        body=_response_body_for_log(response),
                        content_type=response.headers.get("content-type"),
                        attempt=attempt,
                    )
                    if attempt == self._settings.orchestrator_retry_max_attempts:
                        raise HTTPException(status_code=502, detail="Orchestrator upstream failure")
                    continue

                response.raise_for_status()
                parsed = response.json()
                _log_orchestrator_response(
                    settings=self._settings,
                    method="POST",
                    path=path,
                    ctx=ctx,
                    stream=False,
                    status_code=response.status_code,
                    body=parsed,
                    content_type=response.headers.get("content-type"),
                    attempt=attempt,
                )
                return OrchestratorChatResponse.model_validate(
                    normalize_orchestrator_payload(parsed)
                )
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt == self._settings.orchestrator_retry_max_attempts:
                    raise HTTPException(status_code=504, detail="Orchestrator timeout") from exc
            except HTTPException:
                raise
            except Exception as exc:  # pragma: no cover
                last_error = exc
                if attempt == self._settings.orchestrator_retry_max_attempts:
                    raise HTTPException(status_code=502, detail="Orchestrator request failed") from exc

        raise HTTPException(status_code=502, detail=f"Orchestrator error: {last_error}")

    async def _chat_flat(
        self,
        payload: OrchestratorChatRequest,
        ctx: OrchestratorCallContext,
        *,
        log_note: str | None = None,
    ) -> OrchestratorChatResponse:
        """POST flat headers + JSON body with retries."""
        flat_ctx = OrchestratorCallContext(
            session_id=ctx.session_id,
            request_id=ctx.request_id,
            trace_id=ctx.trace_id,
            user_id=ctx.user_id,
            roles=ctx.roles,
            groups=ctx.groups,
            teams=ctx.teams,
            stream=False,
            conversation_id=ctx.conversation_id,
        )
        path = self._settings.orchestrator_chat_path
        headers = self._flat_headers(flat_ctx)
        body = self._flat_json_body(payload, flat_ctx)
        last_error: Exception | None = None
        for attempt in range(1, self._settings.orchestrator_retry_max_attempts + 1):
            try:
                _log_orchestrator_request(
                    settings=self._settings,
                    method="POST",
                    path=path,
                    ctx=flat_ctx,
                    stream=False,
                    headers=headers,
                    body=body,
                    attempt=attempt,
                    note=log_note or "non_stream_chat",
                )
                response = await self._client.post(path, headers=headers, json=body)
                if response.status_code == status.HTTP_400_BAD_REQUEST:
                    _log_orchestrator_response(
                        settings=self._settings,
                        method="POST",
                        path=path,
                        ctx=flat_ctx,
                        stream=False,
                        status_code=response.status_code,
                        body=_response_body_for_log(response),
                        content_type=response.headers.get("content-type"),
                        attempt=attempt,
                    )
                    raise HTTPException(status_code=400, detail="Invalid request for orchestrator")
                if response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
                    _log_orchestrator_response(
                        settings=self._settings,
                        method="POST",
                        path=path,
                        ctx=flat_ctx,
                        stream=False,
                        status_code=response.status_code,
                        body=_response_body_for_log(response),
                        content_type=response.headers.get("content-type"),
                        attempt=attempt,
                    )
                    raise HTTPException(status_code=response.status_code, detail="Upstream auth failure")
                if response.status_code >= 500:
                    _log_orchestrator_response(
                        settings=self._settings,
                        method="POST",
                        path=path,
                        ctx=flat_ctx,
                        stream=False,
                        status_code=response.status_code,
                        body=_response_body_for_log(response),
                        content_type=response.headers.get("content-type"),
                        attempt=attempt,
                    )
                    if attempt == self._settings.orchestrator_retry_max_attempts:
                        raise HTTPException(status_code=502, detail="Orchestrator upstream failure")
                    continue

                response.raise_for_status()
                parsed = response.json()
                _log_orchestrator_response(
                    settings=self._settings,
                    method="POST",
                    path=path,
                    ctx=flat_ctx,
                    stream=False,
                    status_code=response.status_code,
                    body=parsed,
                    content_type=response.headers.get("content-type"),
                    attempt=attempt,
                )
                return OrchestratorChatResponse.model_validate(
                    normalize_orchestrator_payload(parsed)
                )
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt == self._settings.orchestrator_retry_max_attempts:
                    raise HTTPException(status_code=504, detail="Orchestrator timeout") from exc
            except HTTPException:
                raise
            except Exception as exc:  # pragma: no cover
                last_error = exc
                if attempt == self._settings.orchestrator_retry_max_attempts:
                    raise HTTPException(status_code=502, detail="Orchestrator request failed") from exc

        raise HTTPException(status_code=502, detail=f"Orchestrator error: {last_error}")

    async def stream_chat(
        self, payload: OrchestratorChatRequest, ctx: OrchestratorCallContext
    ) -> AsyncGenerator[str, None]:
        """Stream orchestrator output; no HTTP retries after the stream begins (retry only safe pre-stream)."""
        if self._settings.orchestrator_contract == "flat_headers":
            async for ev in self._stream_chat_flat(payload, ctx):
                yield ev
            return
        async for ev in self._stream_chat_gateway_json(payload, ctx):
            yield ev

    async def _stream_chat_gateway_json(
        self, payload: OrchestratorChatRequest, ctx: OrchestratorCallContext
    ) -> AsyncGenerator[str, None]:
        """Stream NDJSON/SSE from orchestrator and map to gateway token events."""
        path = self._settings.orchestrator_chat_path
        body = payload.model_dump()
        try:
            _log_orchestrator_request(
                settings=self._settings,
                method="POST",
                path=path,
                ctx=ctx,
                stream=True,
                headers={"Accept": "text/event-stream"},
                body=body,
            )
            async with self._client.stream(
                "POST",
                path,
                headers={"Accept": "text/event-stream"},
                json=body,
            ) as response:
                if response.status_code >= 400:
                    _log_orchestrator_response(
                        settings=self._settings,
                        method="POST",
                        path=path,
                        ctx=ctx,
                        stream=True,
                        status_code=response.status_code,
                        body=_response_body_for_log(response),
                        content_type=response.headers.get("content-type"),
                        note="stream_failed",
                    )
                    raise HTTPException(status_code=502, detail="Orchestrator stream failed")
                _log_orchestrator_response(
                    settings=self._settings,
                    method="POST",
                    path=path,
                    ctx=ctx,
                    stream=True,
                    status_code=response.status_code,
                    body={"streaming": True},
                    content_type=response.headers.get("content-type"),
                    note="stream_opened",
                )
                rewrite_acc: str | None = None
                timings_acc: dict[str, Any] | None = None
                stream_done_sent = False
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        if line.strip():
                            yield _format_answer_delta_sse_chunk(line)
                        continue
                    if not isinstance(parsed, dict):
                        continue
                    ndjson_timings = orchestrator_workflow_from_source(parsed)
                    if ndjson_timings:
                        timings_acc = ndjson_timings
                    event_type = parsed.get("type")
                    if isinstance(event_type, str):
                        kind = event_type.lower()
                        if kind == "rewrite":
                            text = parsed.get("text")
                            if isinstance(text, str) and text.strip():
                                rewrite_acc = text.strip()
                                yield _format_rewrite_sse_chunk(rewrite_acc)
                            continue
                        if kind == "route":
                            yield _format_route_sse_chunk(parsed)
                            continue
                        if kind in ("answer_delta", "answer"):
                            text = parsed.get("text")
                            if isinstance(text, str) and text:
                                yield _format_answer_delta_sse_chunk(text)
                            continue
                        if kind in ("correlation", "request_id", "state"):
                            continue
                        if kind == "done":
                            done_body = _gateway_done_payload(
                                json.dumps(parsed),
                                citations=[],
                                follow_up_questions=[],
                                rewrite=rewrite_acc,
                                latency_ms=timings_acc,
                            )
                            ndjson_usage = normalize_orchestrator_usage(parsed)
                            if ndjson_usage and not _upstream_has_usage(done_body):
                                done_body["usage"] = ndjson_usage
                            yield _format_done_sse_chunk(done_body)
                            stream_done_sent = True
                            continue
                    text = parsed.get("text") or parsed.get("token") or ""
                    if text:
                        yield _format_answer_delta_sse_chunk(text)
            if not stream_done_sent:
                result = await self._chat_gateway_json(payload, ctx)
                done_body = _done_body_from_orchestrator_result(result)
                if rewrite_acc and not done_body.get("rewrite"):
                    done_body["rewrite"] = rewrite_acc
                if timings_acc and not _upstream_has_workflow_timings(done_body):
                    done_body["latency_ms"] = timings_acc
                _log_orchestrator_response(
                    settings=self._settings,
                    method="POST",
                    path=path,
                    ctx=ctx,
                    stream=True,
                    status_code=200,
                    body=done_body,
                    note="stream_metadata_supplement",
                )
                yield _format_done_sse_chunk(done_body)
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Orchestrator stream timeout") from exc

    async def _stream_chat_flat(
        self, payload: OrchestratorChatRequest, ctx: OrchestratorCallContext
    ) -> AsyncGenerator[str, None]:
        """Stream flat_headers SSE and enrich terminal done metadata."""
        flat_ctx = OrchestratorCallContext(
            session_id=ctx.session_id,
            request_id=ctx.request_id,
            trace_id=ctx.trace_id,
            user_id=ctx.user_id,
            roles=ctx.roles,
            groups=ctx.groups,
            teams=ctx.teams,
            stream=True,
            conversation_id=ctx.conversation_id,
        )
        path = self._settings.orchestrator_chat_path
        headers = {**self._flat_headers(flat_ctx), "Accept": "text/event-stream"}
        body = self._flat_json_body(payload, flat_ctx)
        try:
            _log_orchestrator_request(
                settings=self._settings,
                method="POST",
                path=path,
                ctx=flat_ctx,
                stream=True,
                headers=headers,
                body=body,
                note="flat_stream_primary",
            )
            async with self._client.stream(
                "POST",
                path,
                headers=headers,
                json=body,
            ) as response:
                if response.status_code >= 400:
                    _log_orchestrator_response(
                        settings=self._settings,
                        method="POST",
                        path=path,
                        ctx=flat_ctx,
                        stream=True,
                        status_code=response.status_code,
                        body=_response_body_for_log(response),
                        content_type=response.headers.get("content-type"),
                        note="stream_failed",
                    )
                    raise HTTPException(status_code=502, detail="Orchestrator stream failed")
                _log_orchestrator_response(
                    settings=self._settings,
                    method="POST",
                    path=path,
                    ctx=flat_ctx,
                    stream=True,
                    status_code=response.status_code,
                    body={"streaming": True},
                    content_type=response.headers.get("content-type"),
                    note="stream_opened",
                )
                chunk_gen, used_json_envelope = await _map_flat_upstream_response(response)
                supplement = (
                    None
                    if used_json_envelope
                    else self._flat_stream_metadata_supplement(payload, flat_ctx)
                )
                pending_done_chunk: str | None = None
                streamed_chunk_count = 0
                async for token_chunk in chunk_gen:
                    if token_chunk.lstrip().startswith("event: done"):
                        pending_done_chunk = token_chunk
                        continue
                    streamed_chunk_count += 1
                    yield token_chunk
                supplement_ran_holder: list[bool] = [False]
                enriched_done = await _enrich_stream_done_chunks(
                    [pending_done_chunk] if pending_done_chunk else [],
                    supplement,
                    streamed_before_done=streamed_chunk_count,
                    supplement_ran_out=supplement_ran_holder,
                )
                done_chunk = enriched_done[-1] if enriched_done else _format_done_sse_chunk({"status": "success"})
                done_summary = _parse_done_sse_chunk(done_chunk)
                _log_orchestrator_response(
                    settings=self._settings,
                    method="POST",
                    path=path,
                    ctx=flat_ctx,
                    stream=True,
                    status_code=response.status_code,
                    body={
                        "gateway_chunk_count": streamed_chunk_count + 1,
                        "streamed_before_done": streamed_chunk_count,
                        "used_json_envelope": used_json_envelope,
                        "metadata_supplement": supplement_ran_holder[0],
                        "metadata_supplement_skipped": supplement is not None
                        and not supplement_ran_holder[0],
                        "done": done_summary,
                    },
                    note="stream_closed",
                )
                yield done_chunk
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Orchestrator stream timeout") from exc

    def _flat_stream_metadata_supplement(
        self, payload: OrchestratorChatRequest, stream_ctx: OrchestratorCallContext
    ) -> Callable[
        [], Awaitable[tuple[list[dict[str, Any]], list[str], dict[str, Any] | None, dict[str, Any]]]
    ]:
        """When upstream SSE omits metadata, fetch citations / follow-ups / timings / usage via non-stream JSON."""

        async def _fetch() -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None, dict[str, Any]]:
            """Run one non-stream chat to fetch stream metadata missing from SSE."""
            non_stream_ctx = OrchestratorCallContext(
                session_id=stream_ctx.session_id,
                request_id=stream_ctx.request_id,
                trace_id=stream_ctx.trace_id,
                user_id=stream_ctx.user_id,
                roles=stream_ctx.roles,
                groups=stream_ctx.groups,
                teams=stream_ctx.teams,
                stream=False,
                conversation_id=stream_ctx.conversation_id,
            )
            result = await self._chat_flat(
                payload, non_stream_ctx, log_note="flat_stream_metadata_supplement"
            )
            cites = [c for c in (result.citations or []) if isinstance(c, dict)]
            follow_ups = [
                str(q).strip()
                for q in (result.follow_up_questions or [])
                if isinstance(q, str) and str(q).strip()
            ]
            return (
                cites,
                follow_ups,
                orchestrator_workflow_from_source(result),
                normalize_orchestrator_usage(result),
            )

        return _fetch

    async def readiness_check(self) -> tuple[bool, str | None]:
        """GET configured path on orchestrator base URL; used by gateway ``/ready``."""
        if not self._settings.orchestrator_readiness_probe_enabled:
            return True, None
        path = self._settings.orchestrator_readiness_path
        timeout = httpx.Timeout(self._settings.orchestrator_readiness_timeout_ms / 1000)
        try:
            response = await self._client.get(path, timeout=timeout)
        except httpx.TimeoutException:
            return False, "orchestrator readiness probe timed out"
        except httpx.RequestError as exc:
            return False, f"orchestrator unreachable: {exc}"
        if response.status_code < 200 or response.status_code >= 300:
            return False, f"orchestrator returned HTTP {response.status_code}"
        return True, None

    async def post_feedback(self, body: dict[str, Any]) -> tuple[int, dict[str, Any] | list[Any] | None]:
        """POST feedback JSON to orchestrator; returns (status_code, parsed_json_or_none)."""
        path = self._settings.orchestrator_feedback_path
        _log_orchestrator_request(
            settings=self._settings,
            method="POST",
            path=path,
            ctx=None,
            stream=False,
            body=body,
        )
        response = await self._client.post(path, json=body)
        if not response.content:
            _log_orchestrator_response(
                settings=self._settings,
                method="POST",
                path=path,
                ctx=None,
                stream=False,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                body=None,
            )
            return response.status_code, None
        try:
            parsed = response.json()
            _log_orchestrator_response(
                settings=self._settings,
                method="POST",
                path=path,
                ctx=None,
                stream=False,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                body=parsed,
            )
            return response.status_code, parsed
        except json.JSONDecodeError:
            raw = response.text
            _log_orchestrator_response(
                settings=self._settings,
                method="POST",
                path=path,
                ctx=None,
                stream=False,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                body=raw,
            )
            return response.status_code, None


def _sse_items_from_payload(parsed: Any) -> list[Any]:
    """RAG/orchestrator often wraps lists in ``{"items": [...]}``."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        items = parsed.get("items")
        if isinstance(items, list):
            return items
    return []


def _orchestrator_sse_ndjson_type(raw: str) -> tuple[str | None, dict[str, Any] | None]:
    """Parse orchestrator ``data: {"type": ...}`` NDJSON payloads."""
    if not raw.strip():
        return None, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    event_type = parsed.get("type")
    if isinstance(event_type, str):
        return event_type.lower(), parsed
    return None, parsed


def _format_answer_delta_sse_chunk(text: str) -> str:
    """Format one gateway ``answer_delta`` SSE event (shared token contract)."""
    return f"event: answer_delta\ndata: {json.dumps({'text': text})}\n\n"


def _format_rewrite_sse_chunk(text: str) -> str:
    """Format one gateway ``rewrite`` SSE event."""
    return f"event: rewrite\ndata: {json.dumps({'text': text})}\n\n"


def _format_route_sse_chunk(route_payload: dict[str, Any]) -> str:
    """Format one gateway ``route`` SSE event (passthrough orchestrator shape)."""
    return f"event: route\ndata: {json.dumps(route_payload)}\n\n"


def _token_text_from_sse_data(raw: str) -> str:
    """Extract display text from upstream SSE data line."""
    if not raw.strip():
        return ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            event_type = parsed.get("type")
            if isinstance(event_type, str) and event_type.lower() in (
                "rewrite",
                "route",
                "done",
                "correlation",
                "request_id",
                "state",
                "error",
            ):
                return ""
            text = parsed.get("text") or parsed.get("token")
            return str(text) if text else ""
        if isinstance(parsed, str):
            return parsed
    except json.JSONDecodeError:
        return raw
    return ""


def _done_body_from_orchestrator_result(result: OrchestratorChatResponse) -> dict[str, Any]:
    """Build gateway done payload from non-stream orchestrator result."""
    raw = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    normalized = normalize_orchestrator_payload(raw)
    body = gateway_done_fields_from_normalized(normalized)
    if not body.get("status"):
        body["status"] = "success"
    orch_workflow = orchestrator_workflow_from_source(normalized)
    if orch_workflow and not body.get("latency_ms"):
        body["latency_ms"] = orch_workflow
    usage = normalize_orchestrator_usage(normalized)
    if usage:
        body["usage"] = usage
    return body


def _parse_done_sse_chunk(chunk: str) -> dict[str, Any] | None:
    """Parse ``event: done`` data JSON from one SSE chunk."""
    for line in chunk.splitlines():
        if line.startswith("data:"):
            raw = line[5:].strip()
            if not raw:
                return {"status": "success"}
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {"status": "success"}
            except json.JSONDecodeError:
                return {"status": "success"}
    return None


def _format_done_sse_chunk(done_body: dict[str, Any]) -> str:
    """Format one gateway ``done`` SSE event."""
    return f"event: done\ndata: {json.dumps(done_body)}\n\n"


async def _gateway_chunks_from_orchestrator_json(parsed: dict[str, Any]) -> AsyncGenerator[str, None]:
    """Emit gateway SSE chunks from one orchestrator JSON envelope (non-SSE stream response)."""
    normalized = normalize_orchestrator_payload(parsed)

    rewrite = normalized.get("rewrite")
    if isinstance(rewrite, str) and rewrite.strip():
        yield _format_rewrite_sse_chunk(rewrite.strip())

    route_evt = parsed.get("route")
    if isinstance(route_evt, dict):
        yield _format_route_sse_chunk(route_evt)

    answer = normalized.get("answer")
    if isinstance(answer, str) and answer:
        yield _format_answer_delta_sse_chunk(answer)

    done_body = gateway_done_fields_from_normalized(normalized)
    if not done_body.get("status"):
        done_body["status"] = "success"
    orch_workflow = orchestrator_workflow_from_source(normalized)
    if orch_workflow and not done_body.get("latency_ms"):
        done_body["latency_ms"] = orch_workflow
    usage = normalize_orchestrator_usage(normalized)
    if usage:
        done_body["usage"] = usage
    yield _format_done_sse_chunk(done_body)


async def _map_buffered_flat_upstream(
    response: httpx.Response,
) -> tuple[AsyncGenerator[str, None], bool]:
    """Buffer full upstream body (``application/json`` or unknown content-type)."""
    content_type = (response.headers.get("content-type") or "").lower()
    raw = await response.aread()
    if not raw.strip():

        async def _empty() -> AsyncGenerator[str, None]:
            if False:
                yield ""

        return _empty(), False

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict) and _looks_like_orchestrator_json_envelope(parsed):

        async def _json_gen() -> AsyncGenerator[str, None]:
            async for chunk in _gateway_chunks_from_orchestrator_json(parsed):
                yield chunk

        return _json_gen(), True

    async def _sse_gen() -> AsyncGenerator[str, None]:
        async for chunk in _iter_upstream_sse_lines(_lines_from_bytes(raw)):
            yield chunk

    if not content_type or "text/event-stream" in content_type:
        return _sse_gen(), False

    log_event(
        "flat_upstream_body_unrecognized",
        level="WARN",
        content_type=content_type,
        body_preview=raw[:512].decode(errors="replace"),
    )

    async def _empty_gen() -> AsyncGenerator[str, None]:
        return
        yield ""  # pragma: no cover — async generator marker

    return _empty_gen(), False


async def _map_live_flat_upstream_sse(
    response: httpx.Response,
) -> tuple[AsyncGenerator[str, None], bool]:
    """Passthrough orchestrator SSE so gateway tokens reach the browser as they arrive.

    Sniffs initial lines for a raw JSON envelope (orchestrator sometimes labels JSON as SSE).
    """
    buffered_lines: list[str] = []
    line_iter = response.aiter_lines()
    async for line in line_iter:
        buffered_lines.append(line)
        probe = "\n".join(buffered_lines).strip()
        if not probe:
            continue
        if probe.startswith("data:") or probe.startswith("event:") or probe.startswith(":"):
            break
        if "\ndata:" in probe or "\nevent:" in probe:
            break
        if probe.startswith("{"):
            try:
                parsed = json.loads(probe)
            except json.JSONDecodeError:
                if len(buffered_lines) > 500:
                    break
                continue
            if isinstance(parsed, dict) and _looks_like_orchestrator_json_envelope(parsed):

                async def _json_gen() -> AsyncGenerator[str, None]:
                    async for out in _gateway_chunks_from_orchestrator_json(parsed):
                        yield out

                return _json_gen(), True

    async def _all_lines():
        for ln in buffered_lines:
            yield ln
        async for ln in line_iter:
            yield ln

    async def _live_sse() -> AsyncGenerator[str, None]:
        async for out in _iter_upstream_sse_lines(_all_lines()):
            yield out

    return _live_sse(), False


async def _map_flat_upstream_response(
    response: httpx.Response,
) -> tuple[AsyncGenerator[str, None], bool]:
    """Return gateway chunk generator and whether upstream was a terminal JSON envelope."""
    content_type = (response.headers.get("content-type") or "").lower()
    if "application/json" in content_type and "text/event-stream" not in content_type:
        return await _map_buffered_flat_upstream(response)
    if "text/event-stream" in content_type or not content_type:
        return await _map_live_flat_upstream_sse(response)
    return await _map_buffered_flat_upstream(response)


async def _lines_from_bytes(raw: bytes):
    """Yield lines from a buffered upstream body."""
    for line in raw.decode(errors="replace").splitlines():
        yield line


def _looks_like_orchestrator_json_envelope(parsed: Any) -> bool:
    """True when parsed JSON is a terminal orchestrator answer envelope (not an SSE line)."""
    if not isinstance(parsed, dict):
        return False
    if isinstance(parsed.get("answer"), (dict, str)):
        return True
    if isinstance(parsed.get("meta"), dict):
        return True
    status = parsed.get("status")
    if isinstance(status, dict) and ("ok" in status or "state" in status):
        return True
    return False


def _stream_done_metadata_incomplete(done_body: dict[str, Any]) -> bool:
    """True only when ``done`` is missing fields a non-stream replay could fill (not empty lists)."""
    if not _upstream_has_usage(done_body):
        return True
    if not _upstream_has_workflow_timings(done_body):
        return True
    if "citations" not in done_body:
        return True
    if "follow_up_questions" not in done_body:
        return True
    return False


async def _enrich_stream_done_chunks(
    chunks: list[str],
    supplement: Callable[
        [], Awaitable[tuple[list[dict[str, Any]], list[str], dict[str, Any] | None, dict[str, Any]]]
    ]
    | None,
    *,
    streamed_before_done: int = 0,
    supplement_ran_out: list[bool] | None = None,
) -> list[str]:
    """Ensure terminal ``done`` includes citations, follow-ups, timings, and usage (supplement when missing)."""
    done_idx = -1
    for i, chunk in enumerate(chunks):
        if chunk.lstrip().startswith("event: done"):
            done_idx = i
    done_body: dict[str, Any] = {"status": "success"}
    if done_idx >= 0:
        parsed = _parse_done_sse_chunk(chunks[done_idx])
        if parsed:
            done_body = parsed
    elif supplement is None:
        return chunks

    if "citations" not in done_body:
        done_body["citations"] = []
    if "follow_up_questions" not in done_body:
        done_body["follow_up_questions"] = []

    # Answer already streamed (typical greeting / direct_reply SSE): do not replay orchestrator with stream=false.
    skip_supplement = streamed_before_done > 0
    needs_supplement = (
        supplement is not None and not skip_supplement and _stream_done_metadata_incomplete(done_body)
    )
    if needs_supplement:
        try:
            s_cites, s_follows, s_timings, s_usage = await supplement()
            if s_cites and not done_body.get("citations"):
                done_body["citations"] = s_cites
            if s_follows and not done_body.get("follow_up_questions"):
                done_body["follow_up_questions"] = s_follows
            if s_timings and not _upstream_has_workflow_timings(done_body):
                done_body["latency_ms"] = s_timings
            if s_usage and not _upstream_has_usage(done_body):
                done_body["usage"] = s_usage
        except HTTPException:
            raise
        except Exception as exc:
            log_event("stream_metadata_supplement_failed", level="WARN", error=str(exc))
        if supplement_ran_out is not None:
            supplement_ran_out[0] = True

    enriched = _format_done_sse_chunk(done_body)
    if done_idx >= 0:
        chunks[done_idx] = enriched
    else:
        chunks.append(enriched)
    return chunks


def _gateway_done_payload(
    raw: str,
    *,
    citations: list[dict[str, Any]],
    follow_up_questions: list[str],
    rewrite: str | None = None,
    latency_ms: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build gateway ``done`` data, merging upstream terminal event with accumulated RAG fields."""
    body: dict[str, Any] = {"status": "success"}
    if rewrite:
        body["rewrite"] = rewrite
    if citations:
        body["citations"] = citations
    if follow_up_questions:
        body["follow_up_questions"] = follow_up_questions
    if not raw.strip():
        if latency_ms and not body.get("latency_ms"):
            body["latency_ms"] = latency_ms
        return body
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        if latency_ms and not body.get("latency_ms"):
            body["latency_ms"] = latency_ms
        return body
    if not isinstance(parsed, dict):
        if latency_ms and not body.get("latency_ms"):
            body["latency_ms"] = latency_ms
        return body

    normalized = normalize_orchestrator_payload(parsed)
    merged = gateway_done_fields_from_normalized(normalized)
    if merged.get("status"):
        body["status"] = merged["status"]
    for key in (
        "rewrite",
        "citations",
        "follow_up_questions",
        "route",
        "route_detail",
        "route_source",
        "latency_ms",
        "usage",
        "rag",
    ):
        val = merged.get(key)
        if val:
            body[key] = val

    if rewrite and not body.get("rewrite"):
        body["rewrite"] = rewrite
    if citations and not body.get("citations"):
        body["citations"] = citations
    if follow_up_questions and not body.get("follow_up_questions"):
        body["follow_up_questions"] = follow_up_questions

    upstream_timings = orchestrator_workflow_from_source(normalized)
    if upstream_timings:
        body["latency_ms"] = upstream_timings
    elif latency_ms and not body.get("latency_ms"):
        body["latency_ms"] = latency_ms

    upstream_usage = normalize_orchestrator_usage(normalized)
    if upstream_usage:
        body["usage"] = upstream_usage
    return body


async def _iter_upstream_sse_as_gateway_tokens(response: httpx.Response) -> AsyncGenerator[str, None]:
    """Map upstream RAG SSE (``answer_delta``, ``citations``, ``follow_up_questions``, ``done``) to gateway contract."""
    async for chunk in _iter_upstream_sse_lines(response.aiter_lines()):
        yield chunk


async def _iter_upstream_sse_lines(
    lines: Any,
) -> AsyncGenerator[str, None]:
    """Map upstream SSE lines to gateway contract (live stream or buffered body)."""
    block_lines: list[str] = []
    citations_acc: list[dict[str, Any]] = []
    follow_ups_acc: list[str] = []
    rewrite_acc: str | None = None
    timings_acc: dict[str, Any] | None = None
    usage_acc: dict[str, Any] | None = None
    async for line in lines:
        if line.startswith(":"):
            continue
        if line == "":
            if not block_lines:
                continue
            event_name = "message"
            data_lines: list[str] = []
            for bl in block_lines:
                if bl.startswith("event:"):
                    event_name = bl[6:].strip().lower()
                elif bl.startswith("data:"):
                    data_lines.append(bl[5:].lstrip())
            block_lines.clear()
            raw = "\n".join(data_lines)

            ndjson_type, ndjson = _orchestrator_sse_ndjson_type(raw)
            if ndjson is not None:
                ndjson_timings = orchestrator_workflow_from_source(ndjson)
                if ndjson_timings:
                    timings_acc = ndjson_timings
                ndjson_usage = normalize_orchestrator_usage(ndjson)
                if ndjson_usage:
                    usage_acc = ndjson_usage
            if ndjson_type == "rewrite":
                text = ndjson.get("text") if ndjson else None
                if isinstance(text, str) and text.strip():
                    rewrite_acc = text.strip()
                    yield _format_rewrite_sse_chunk(rewrite_acc)
                continue
            if ndjson_type == "route" and ndjson is not None:
                yield _format_route_sse_chunk(ndjson)
                continue
            if ndjson_type in ("correlation", "request_id", "state"):
                continue
            if ndjson_type == "answer" and ndjson is not None:
                text = ndjson.get("text")
                if isinstance(text, str) and text:
                    yield f"event: answer_delta\ndata: {json.dumps({'text': text})}\n\n"
                for item in ndjson.get("citations") or []:
                    if isinstance(item, dict):
                        citations_acc.append(item)
                for item in ndjson.get("follow_up_questions") or []:
                    if isinstance(item, str) and item.strip():
                        follow_ups_acc.append(item.strip())
                continue
            if ndjson_type == "done":
                done_body = _gateway_done_payload(
                    raw,
                    citations=citations_acc,
                    follow_up_questions=follow_ups_acc,
                    rewrite=rewrite_acc,
                    latency_ms=timings_acc,
                )
                if usage_acc and not _upstream_has_usage(done_body):
                    done_body["usage"] = usage_acc
                yield _format_done_sse_chunk(done_body)
                continue

            if event_name == "done":
                done_body = _gateway_done_payload(
                    raw,
                    citations=citations_acc,
                    follow_up_questions=follow_ups_acc,
                    rewrite=rewrite_acc,
                    latency_ms=timings_acc,
                )
                if usage_acc and not _upstream_has_usage(done_body):
                    done_body["usage"] = usage_acc
                yield _format_done_sse_chunk(done_body)
                continue

            if event_name in ("timings", "timings_ms", "latency", "latency_ms"):
                try:
                    parsed = json.loads(raw) if raw.strip() else {}
                    stream_timings = orchestrator_workflow_from_source(parsed)
                    if stream_timings:
                        timings_acc = stream_timings
                except json.JSONDecodeError:
                    pass
                continue

            if event_name == "citations":
                try:
                    parsed = json.loads(raw) if raw.strip() else {}
                    for item in _sse_items_from_payload(parsed):
                        if isinstance(item, dict):
                            citations_acc.append(item)
                except json.JSONDecodeError:
                    pass
                continue

            if event_name == "follow_up_questions":
                try:
                    parsed = json.loads(raw) if raw.strip() else {}
                    for item in _sse_items_from_payload(parsed):
                        if isinstance(item, str) and item.strip():
                            follow_ups_acc.append(item.strip())
                except json.JSONDecodeError:
                    pass
                continue

            # ``answer_delta``, ``token``, or bare ``data: {"text":...}`` lines
            if event_name in ("answer_delta", "token", "message", ""):
                text = _token_text_from_sse_data(raw)
                if text:
                    yield f"event: answer_delta\ndata: {json.dumps({'text': text})}\n\n"
            continue
        block_lines.append(line)
