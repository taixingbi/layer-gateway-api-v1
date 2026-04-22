import json
from collections.abc import AsyncGenerator

import httpx
from fastapi import HTTPException, status

from app.core.config import Settings
from app.schemas.orchestrator import OrchestratorChatRequest, OrchestratorChatResponse


class OrchestratorClient:
    """Transport adapter responsible for orchestrator chat calls."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self._client = client
        self._settings = settings

    async def chat(self, payload: OrchestratorChatRequest) -> OrchestratorChatResponse:
        """Send non-stream chat requests with bounded retries and mapped errors."""
        last_error: Exception | None = None
        for attempt in range(1, self._settings.orchestrator_retry_max_attempts + 1):
            try:
                # Forward normalized gateway payload to the orchestrator endpoint.
                response = await self._client.post(
                    self._settings.orchestrator_chat_path,
                    json=payload.model_dump(),
                )
                # Preserve validation semantics for frontend-visible 400s.
                if response.status_code == status.HTTP_400_BAD_REQUEST:
                    raise HTTPException(status_code=400, detail="Invalid request for orchestrator")
                # Preserve auth semantics for frontend-visible 401/403s.
                if response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
                    raise HTTPException(status_code=response.status_code, detail="Upstream auth failure")
                # Retry transient upstream failures, then map to 502 at final attempt.
                if response.status_code >= 500:
                    if attempt == self._settings.orchestrator_retry_max_attempts:
                        raise HTTPException(status_code=502, detail="Orchestrator upstream failure")
                    continue

                # Validate successful upstream payload into gateway DTO.
                response.raise_for_status()
                return OrchestratorChatResponse.model_validate(response.json())
            except httpx.TimeoutException as exc:
                # Convert timeout exhaustion into gateway timeout signal.
                last_error = exc
                if attempt == self._settings.orchestrator_retry_max_attempts:
                    raise HTTPException(status_code=504, detail="Orchestrator timeout") from exc
            except HTTPException:
                # Allow explicitly mapped HTTP errors to bubble up unchanged.
                raise
            except Exception as exc:  # pragma: no cover
                # Handle unexpected transport/parsing errors as bad gateway.
                last_error = exc
                if attempt == self._settings.orchestrator_retry_max_attempts:
                    raise HTTPException(status_code=502, detail="Orchestrator request failed") from exc

        # Defensive fallback if control exits loop without returning.
        raise HTTPException(status_code=502, detail=f"Orchestrator error: {last_error}")

    async def stream_chat(self, payload: OrchestratorChatRequest) -> AsyncGenerator[str, None]:
        """Stream orchestrator output and convert chunks to gateway SSE token events."""
        try:
            async with self._client.stream(
                "POST",
                self._settings.orchestrator_chat_path,
                json=payload.model_dump(),
            ) as response:
                # Any non-success upstream stream is surfaced as gateway error.
                if response.status_code >= 400:
                    raise HTTPException(status_code=502, detail="Orchestrator stream failed")
                async for line in response.aiter_lines():
                    # Skip keepalive and empty frames.
                    if not line:
                        continue
                    try:
                        # Prefer structured payload fields when upstream emits JSON lines.
                        parsed = json.loads(line)
                        text = parsed.get("text") or parsed.get("token") or ""
                    except json.JSONDecodeError:
                        # Fall back to raw line for plain-text stream emitters.
                        text = line
                    if text:
                        # Emit normalized token event shape expected by frontend.
                        yield f"event: token\ndata: {json.dumps({'text': text})}\n\n"
        except httpx.TimeoutException as exc:
            # Surface stream timeout as gateway timeout class.
            raise HTTPException(status_code=504, detail="Orchestrator stream timeout") from exc
