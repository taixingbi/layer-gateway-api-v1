# API request and response schemas

Schemas for the **public gateway API** (`POST /api/chat`, `POST /api/feedback`). Source of truth: `app/schemas/*.py` and `app/routes/*.py`.

Correlation IDs (`request_id`, `trace_id`, `session_id`) are taken from **headers** when present; the gateway mints missing values. Do **not** send `session_id`, `request_id`, or `trace_id` in JSON bodies (`extra="forbid"` on chat/feedback request models).

---

## `POST /api/chat`

### Request headers

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <token>` (stub: any non-empty token; JWT when `AUTH_MODE=jwt`) |
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

**Only when** `ORCHESTRATOR_CONTRACT=flat_headers`. Otherwise **501**.

### Request headers

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <token>` |
| `Content-Type` | Yes | `application/json` |

### Request body (`FeedbackRequest`)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `trace_id` | string | Yes | Correlate with chat (`X-Trace-Id` / chat `trace_id`) |
| `request_id` | string | No | Optional chat `request_id` |
| `rating` | string | Yes | `"thumbs_up"` or `"thumbs_down"` |
| `feedback_type` | string | No | Upstream-specific type (e.g. `not_factual`) |
| `comment` | string | No | Free-text comment |
| `question` | string | No | Original user question |

Example:

```json
{
  "trace_id": "trace_demo_001",
  "request_id": "req_demo_001",
  "rating": "thumbs_down",
  "feedback_type": "not_factual",
  "comment": "Answer was incomplete",
  "question": "What is Taixing US visa status?"
}
```

### Response

Proxied from orchestrator:

| HTTP status | Body |
|-------------|------|
| `204` | Empty |
| `2xx` | Upstream JSON (shape depends on orchestrator) |
| `4xx` / `5xx` | Upstream error body or gateway-mapped error |

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
| `400` | Empty message, length exceeded, invalid `X-Session-Id` / `X-Conversation-Id` |
| `401` | Missing/invalid bearer |
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

See also: [design.md](./design.md), [smoke-test.md](./smoke-test.md).
