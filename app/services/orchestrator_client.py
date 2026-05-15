import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.config import Settings
from app.schemas.orchestrator import OrchestratorChatRequest, OrchestratorChatResponse
from app.services.orchestrator_call_context import OrchestratorCallContext


class OrchestratorClient:
    """Transport adapter responsible for orchestrator chat calls."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self._client = client
        self._settings = settings

    def _flat_headers(self, ctx: OrchestratorCallContext) -> dict[str, str]:
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
        body: dict[str, Any] = {"question": payload.input.question, "stream": ctx.stream}
        if ctx.conversation_id:
            body["conversation_id"] = ctx.conversation_id
        return body

    async def chat(self, payload: OrchestratorChatRequest, ctx: OrchestratorCallContext) -> OrchestratorChatResponse:
        """Send non-stream chat requests with bounded retries and mapped errors."""
        if self._settings.orchestrator_contract == "flat_headers":
            return await self._chat_flat(payload, ctx)
        return await self._chat_gateway_json(payload)

    async def _chat_gateway_json(self, payload: OrchestratorChatRequest) -> OrchestratorChatResponse:
        last_error: Exception | None = None
        for attempt in range(1, self._settings.orchestrator_retry_max_attempts + 1):
            try:
                response = await self._client.post(
                    self._settings.orchestrator_chat_path,
                    json=payload.model_dump(),
                )
                if response.status_code == status.HTTP_400_BAD_REQUEST:
                    raise HTTPException(status_code=400, detail="Invalid request for orchestrator")
                if response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
                    raise HTTPException(status_code=response.status_code, detail="Upstream auth failure")
                if response.status_code >= 500:
                    if attempt == self._settings.orchestrator_retry_max_attempts:
                        raise HTTPException(status_code=502, detail="Orchestrator upstream failure")
                    continue

                response.raise_for_status()
                return OrchestratorChatResponse.model_validate(response.json())
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

    async def _chat_flat(self, payload: OrchestratorChatRequest, ctx: OrchestratorCallContext) -> OrchestratorChatResponse:
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
        last_error: Exception | None = None
        for attempt in range(1, self._settings.orchestrator_retry_max_attempts + 1):
            try:
                response = await self._client.post(
                    self._settings.orchestrator_chat_path,
                    headers=self._flat_headers(flat_ctx),
                    json=self._flat_json_body(payload, flat_ctx),
                )
                if response.status_code == status.HTTP_400_BAD_REQUEST:
                    raise HTTPException(status_code=400, detail="Invalid request for orchestrator")
                if response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
                    raise HTTPException(status_code=response.status_code, detail="Upstream auth failure")
                if response.status_code >= 500:
                    if attempt == self._settings.orchestrator_retry_max_attempts:
                        raise HTTPException(status_code=502, detail="Orchestrator upstream failure")
                    continue

                response.raise_for_status()
                return OrchestratorChatResponse.model_validate(response.json())
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
        async for ev in self._stream_chat_gateway_json(payload):
            yield ev

    async def _stream_chat_gateway_json(self, payload: OrchestratorChatRequest) -> AsyncGenerator[str, None]:
        try:
            async with self._client.stream(
                "POST",
                self._settings.orchestrator_chat_path,
                json=payload.model_dump(),
            ) as response:
                if response.status_code >= 400:
                    raise HTTPException(status_code=502, detail="Orchestrator stream failed")
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                        text = parsed.get("text") or parsed.get("token") or ""
                    except json.JSONDecodeError:
                        text = line
                    if text:
                        yield f"event: token\ndata: {json.dumps({'text': text})}\n\n"
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Orchestrator stream timeout") from exc

    async def _stream_chat_flat(
        self, payload: OrchestratorChatRequest, ctx: OrchestratorCallContext
    ) -> AsyncGenerator[str, None]:
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
        try:
            headers = {**self._flat_headers(flat_ctx), "Accept": "text/event-stream"}
            async with self._client.stream(
                "POST",
                self._settings.orchestrator_chat_path,
                headers=headers,
                json=self._flat_json_body(payload, flat_ctx),
            ) as response:
                if response.status_code >= 400:
                    raise HTTPException(status_code=502, detail="Orchestrator stream failed")
                async for token_chunk in _iter_upstream_sse_as_gateway_tokens(response):
                    yield token_chunk
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Orchestrator stream timeout") from exc

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
        response = await self._client.post(self._settings.orchestrator_feedback_path, json=body)
        if not response.content:
            return response.status_code, None
        try:
            return response.status_code, response.json()
        except json.JSONDecodeError:
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


def _token_text_from_sse_data(raw: str) -> str:
    if not raw.strip():
        return ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            text = parsed.get("text") or parsed.get("token")
            return str(text) if text else ""
        if isinstance(parsed, str):
            return parsed
    except json.JSONDecodeError:
        return raw
    return ""


def _gateway_done_payload(
    raw: str,
    *,
    citations: list[dict[str, Any]],
    follow_up_questions: list[str],
) -> dict[str, Any]:
    """Build gateway ``done`` data, merging upstream terminal event with accumulated RAG fields."""
    body: dict[str, Any] = {"status": "success"}
    if citations:
        body["citations"] = citations
    if follow_up_questions:
        body["follow_up_questions"] = follow_up_questions
    if not raw.strip():
        return body
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return body
    if not isinstance(parsed, dict):
        return body
    if parsed.get("status"):
        body["status"] = parsed["status"]
    upstream_cites = parsed.get("citations")
    if isinstance(upstream_cites, list) and upstream_cites:
        body["citations"] = upstream_cites
    upstream_follow = parsed.get("follow_up_questions")
    if isinstance(upstream_follow, list) and upstream_follow:
        body["follow_up_questions"] = [str(q) for q in upstream_follow if q]
    return body


async def _iter_upstream_sse_as_gateway_tokens(response: httpx.Response) -> AsyncGenerator[str, None]:
    """Map upstream RAG SSE (``answer_delta``, ``citations``, ``follow_up_questions``, ``done``) to gateway contract."""
    block_lines: list[str] = []
    citations_acc: list[dict[str, Any]] = []
    follow_ups_acc: list[str] = []
    async for line in response.aiter_lines():
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

            if event_name == "done":
                done_body = _gateway_done_payload(
                    raw, citations=citations_acc, follow_up_questions=follow_ups_acc
                )
                yield f"event: done\ndata: {json.dumps(done_body)}\n\n"
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
                    yield f"event: token\ndata: {json.dumps({'text': text})}\n\n"
            continue
        block_lines.append(line)
