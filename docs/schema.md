# API request and response schemas

Schemas for the **public gateway API** (`POST /api/chat`, `POST /api/feedback`). Source of truth: `app/schemas/*.py` and `app/routes/*.py`.

Correlation IDs (`request_id`, `trace_id`, `session_id`) are taken from **headers** when present; the gateway mints missing values. Do **not** send `session_id`, `request_id`, or `trace_id` in JSON bodies (`extra="forbid"` on chat/feedback request models).

---

## `POST /api/chat`

### Request headers

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <access_token>` (Supabase session JWT or OIDC when using JWKS fallback) |
| `Content-Type` | Yes | `application/json` |
| `Accept` | No | `text/event-stream` to stream; omit for JSON response |
| `X-Session-Id` | No | 3–128 chars; if omitted the gateway mints `sess_<hex>` |
| `X-Conversation-Id` | No | 3–128 chars; **overrides** JSON `conversation_id` when set |
| `X-Request-Id` | No | Echoed on response; minted if omitted |
| `X-Trace-Id` | No | Echoed on response; minted if omitted |

### Request body (`ChatRequest`)

Unknown JSON keys are rejected (`422`).

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `message` | string | Yes | 1–4000 chars after trim (config: `CHAT_MESSAGE_MAX_LENGTH`) |
| `history` | array | No | Up to 100 prior turns; see [History message](#history-message). Default `[]` |
| `conversation_id` | string | No | 3–128 chars |
| `stream` | boolean | No | Default `false`; use with `Accept: text/event-stream` for SSE |
| `client_timestamp` | string (ISO 8601) | No | Opaque client timestamp |
| `metadata` | object | No | Default `{}`; forwarded to orchestrator client info |

**Rejected in body:** `session_id`, `request_id`, `trace_id`, and any other extra keys.

Example:

```json
{
  "conversation_id": "conv_456",
  "message": "What is the return policy?",
  "history": [
    {"role": "user", "content": "What is Taixing Bi US visa status?"},
    {"role": "assistant", "content": "Taixing has H4 EAD and does not need sponsorship."}
  ],
  "stream": false,
  "client_timestamp": "2026-04-22T10:00:00Z",
  "metadata": {
    "page": "/support",
    "user_agent": "curl"
  }
}
```

### Response headers (success)

| Header | Description |
|--------|-------------|
| `X-Request-Id` | Effective request id |
| `X-Trace-Id` | Effective trace id |

---

## `POST /api/chat` — non-stream response (`ChatResponse`)

**HTTP 200**, `Content-Type: application/json`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | Yes | `"success"` on success |
| `session_id` | string | Yes | From `X-Session-Id` or gateway-minted |
| `request_id` | string | Yes | From `X-Request-Id` or gateway-minted |
| `trace_id` | string | Yes | From `X-Trace-Id` or gateway-minted |
| `conversation_id` | string | No | UUID when chat history persistence ran |
| `answer` | string | Yes | Assistant text |
| `rewrite` | string | No | Intent-router rewritten question; omitted when absent |
| `citations` | array | Yes | Default `[]`; see [Citation object](#citation-object) |
| `follow_up_questions` | array of string | Yes | Default `[]` |
| `usage` | object | Yes | Token usage |
| `usage.input_tokens` | integer | Yes | Default `0` |
| `usage.output_tokens` | integer | Yes | Default `0` |
| `error` | object | No | Omitted on success; see [Error object](#error-object) |

Example:

```json
{
  "status": "success",
  "session_id": "sess_123",
  "request_id": "req_demo_001",
  "trace_id": "trace_demo_001",
  "answer": "H4 EAD. No visa sponsorship required. [1]",
  "citations": [
    {
      "cite_id": 1,
      "chunk_id": "1607b45e-1c07-5c29-975d-bbf47ef3129c",
      "source": "personal_profile",
      "text": "Q: What is Taixing Bi's visa status / work authorization?\nA: H4 EAD. No visa sponsorship required."
    }
  ],
  "follow_up_questions": [
    "Can you explain what an H4 EAD means?",
    "Does Taixing need to renew the H4 EAD periodically?"
  ],
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0
  }
}
```

---

## `POST /api/chat` — stream response (SSE)

Enable with **`Accept: text/event-stream`** and/or **`"stream": true`** in the JSON body.

**HTTP 200**, `Content-Type: text/event-stream`.

Each event block:

```text
event: <name>
data: <json>

```

### `event: meta`

Emitted first by the gateway (correlation for the client).

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | |
| `trace_id` | string | |
| `session_id` | string | |
| `conversation_id` | string | Present when resolved |

### `event: rewrite`

Intent-router rewritten question (emitted before answer tokens when upstream provides it).

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | Rewritten question |

### `event: token`

Incremental answer text.

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | Token / delta |

### `event: done`

Terminal success (metadata may be filled from upstream SSE and/or a supplemental non-stream upstream call when the stream lacked citation events).

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"success"` |
| `rewrite` | string | Optional; same as `event: rewrite` when not emitted earlier |
| `citations` | array | Optional; [Citation object](#citation-object) |
| `follow_up_questions` | array of string | Optional |

Example:

```text
event: meta
data: {"request_id":"req_demo_002","trace_id":"trace_demo_002","session_id":"sess_123","conversation_id":"conv_000"}

event: token
data: {"text":"Taixing Bi's visa status in the US is H4 EAD [1]."}

event: done
data: {"status":"success","citations":[{"cite_id":1,"source":"personal_profile","text":"..."}],"follow_up_questions":["What does H4 EAD mean?"]}
```

### `event: error`

Stream failure envelope.

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"error"` |
| `error` | object | |
| `error.code` | string | e.g. upstream status or `upstream_internal` |
| `error.message` | string | Human-readable message |

---

## `POST /api/feedback`

Persists to Supabase **`message_feedback`** when configured. Optionally proxies to orchestrator when `ORCHESTRATOR_CONTRACT=flat_headers` and `trace_id` is set.

### Request headers

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <token>` |
| `Content-Type` | Yes | `application/json` |

### Request body (`FeedbackRequest`)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message_id` | string (UUID) | Yes | Assistant/user message id from chat persistence |
| `conversation_id` | string (UUID) | Yes | Owned conversation |
| `reviewer_type` | string | No | Default `end_user` (`ai_evaluator`, `qa_team`, `rlhf_annotator`, …) |
| `feedback_type` | string | No | e.g. `thumbs_up`, `hallucination`, `bad_citation` |
| `feedback` | int | No | `-1`, `0`, or `1` (derived from `rating` when omitted) |
| `preference_score` | int | No | `1`–`5` (defaults from `rating` when omitted) |
| `model` | string | No | Model name |
| `route` | string | No | e.g. `rag`, `direct_llm` |
| `prompt_version` | string | No | Prompt version label |
| `feedback_comment` | string | No | End-user comment (alias: `comment`) |
| `labeler_notes` | string | No | RLHF / QA notes |
| `metadata` | object | No | Extra jsonb (latency, retrieval_chunks, …) |
| `rating` | string | No | Legacy UI: `thumbs_up` / `thumbs_down` |
| `trace_id` | string | No | Legacy orchestrator correlation |
| `request_id` | string | No | Legacy chat request id |
| `question` | string | No | Stored in `metadata.question` when set |

Example:

```json
{
  "message_id": "2f7f4f4d-12d7-4d92-a6ef-5e3e9c1c5f91",
  "conversation_id": "da26bbf4-8122-4f82-a9d1-0077f02c9d0c",
  "reviewer_type": "end_user",
  "feedback_type": "hallucination",
  "feedback": -1,
  "preference_score": 1,
  "model": "qwen2.5-7b",
  "route": "rag",
  "feedback_comment": "Wrong visa answer",
  "metadata": { "latency_ms": 1820, "retrieval_chunks": 4 }
}
```

### Response (`FeedbackResponse`)

| HTTP status | Body |
|-------------|------|
| `200` | `{ "id", "message_id", "conversation_id", "status": "created" }` |
| `204` | Legacy orchestrator-only success (no Supabase) |
| `400` | Validation / schema mismatch |
| `404` | Message not in conversation |
| `503` | Supabase not configured |

---

## Shared types

### History message

Used in `POST /api/chat` `history` and forwarded to the orchestrator.

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `role` | string | Yes | `"user"` or `"assistant"` |
| `content` | string | Yes | 1–4000 chars after trim |

### Citation object

Opaque object array; typical RAG fields:

| Field | Type | Example |
|-------|------|---------|
| `cite_id` | integer | `1` |
| `chunk_id` | string | UUID |
| `source` | string | `"personal_profile"` |
| `text` | string | Retrieved chunk text |

### Error object (`ErrorDetails`)

Used in JSON `ChatResponse.error` when present.

| Field | Type | Description |
|-------|------|-------------|
| `code` | string | Machine-oriented code |
| `message` | string | Human-readable message |
| `details` | object | Optional extra context |

### Common HTTP errors (`POST /api/chat`)

| Status | When |
|--------|------|
| `400` | Empty message, length exceeded, invalid `X-Session-Id` / `X-Conversation-Id`, invalid UUID `conversation_id` when persisting |
| `401` | Missing/invalid bearer |
| `404` | Unknown or unowned `conversation_id` when persisting or loading history |
| `422` | JSON validation (unknown fields, bad types) |
| `502` / `504` | Orchestrator failure / timeout |
| `503` | Inflight limit (`MAX_INFLIGHT_REQUESTS`) |

---

## Orchestrator mapping (internal)

Not exposed directly to browsers; documented for operators.

### `ORCHESTRATOR_CONTRACT=gateway_json`

Gateway → orchestrator body: nested `OrchestratorChatRequest` (`auth`, `context`, `input`, `client`).

### `ORCHESTRATOR_CONTRACT=flat_headers`

Gateway → orchestrator:

**Headers:** `X-Session-Id`, `X-Request-Id`, `X-Trace-Id`, `X-User-Id`, `X-User-Roles`, `X-User-Groups`, `X-User-Teams`, optional `X-Conversation-Id`.

**JSON body:**

| Field | Type | Description |
|-------|------|-------------|
| `question` | string | From `message` |
| `stream` | boolean | Stream mode |
| `conversation_id` | string | When set |
| `history` | array | When non-empty; same shape as [History message](#history-message) |

**`gateway_json`:** history is under `input.history` in the nested orchestrator body (alongside `input.question`).

Upstream non-stream JSON (`OrchestratorChatResponse`): `answer`, `citations`, `follow_up_questions`, `usage`.

---

## `GET /api/conversations`

### Request headers

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <access_token>` |

### Query parameters

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | integer | `50` | 1–100 |

### Response (`ConversationListResponse`)

| Field | Type | Description |
|-------|------|-------------|
| `conversations` | array | Newest `updated_at` first |
| `conversations[].id` | string | UUID |
| `conversations[].title` | string | Optional |
| `conversations[].created_at` | string | ISO 8601 (EST) |
| `conversations[].updated_at` | string | ISO 8601 (EST) |

---

## `GET /api/conversations/{conversation_id}/messages`

### Request headers

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <access_token>` |

### Response (`ConversationMessagesResponse`)

| Field | Type | Description |
|-------|------|-------------|
| `conversation_id` | string | UUID path parameter |
| `messages` | array | Ordered by `created_at` ascending |
| `messages[].id` | string (UUID) | Message id |
| `messages[].role` | string | `user` or `assistant` |
| `messages[].content` | string | Message text |
| `messages[].status` | string | Optional; assistant rows use `complete` |
| `messages[].metadata` | object | Optional jsonb; assistant may include `rewrite`, `citations`, `follow_up_questions`, `model` |
| `messages[].created_at` | string | Optional ISO 8601 (EST) |

| Status | When |
|--------|------|
| `404` | Conversation not found or not owned by caller |

---

See also: [design.md](./design.md), [smoke-test.md](./smoke-test.md).
