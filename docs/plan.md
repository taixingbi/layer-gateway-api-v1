# Gateway Decoupling Plan

## Goal
Move all AI-facing logic out of Next.js and into this Gateway API.

## Scope
- Next.js handles UI only.
- Gateway handles authentication context, request metadata, validation, observability, and orchestrator communication.
- Orchestrator handles workflow, tools, retrieval, prompt assembly, and LLM execution.

## Phases
1. Define API contracts.
2. Build gateway skeleton.
3. Integrate orchestrator client with timeout and retry.
4. Move frontend traffic to gateway only.
5. Harden observability and reliability.

## Implemented MVP
- Auth middleware for bearer token checks and trusted auth context attachment.
- Request context middleware for `request_id` and `trace_id` generation/propagation.
- `POST /api/chat` with normalization and validation.
- Stable response contract for non-stream responses.
- SSE mode for stream responses (`meta`, `token`, `done`, `error` events).
- Orchestrator client with timeout/retry and upstream error mapping.
- Structured JSON logging events and `GET /health`.
- Tests for auth, validation, IDs, streaming contract, and retry/timeout mapping.

## Next Actions
- Replace auth stub with real JWT/IdP verification.
- Standardize non-2xx error envelope across all handlers.
- Add metrics, rate limits, and circuit breaker.
- Add OpenAPI examples and consumer docs for frontend integration.
