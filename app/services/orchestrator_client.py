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


async def _iter_upstream_sse_as_gateway_tokens(response: httpx.Response) -> AsyncGenerator[str, None]:
    """Parse upstream SSE and emit gateway `event: token` frames."""
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith(":"):
            continue
        if line == "":
            if not data_lines:
                continue
            raw = "\n".join(data_lines)
            data_lines.clear()
            text = ""
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    text = str(parsed.get("text") or parsed.get("token") or "")
                elif isinstance(parsed, str):
                    text = parsed
            except json.JSONDecodeError:
                text = raw
            if text:
                yield f"event: token\ndata: {json.dumps({'text': text})}\n\n"
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
