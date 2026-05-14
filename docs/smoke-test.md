# Smoke tests (curl)

Quick checks against a running gateway. Replace the host/port in each example if yours differs (e.g. `http://localhost:8000`).

Stub auth accepts any non-empty bearer token (see `app/middleware/auth.py`).

## No auth (probes and metrics)

**Liveness**

```bash
curl -sS "http://192.168.86.179:30185/health" | jq .
```

**Readiness** (calls orchestrator `GET` on `ORCHESTRATOR_READINESS_PATH`; may be **503** if upstream is down or probe is disabled)

```bash
curl -sS "http://192.168.86.179:30185/ready" | jq .
```

**Prometheus scrape**

```bash
curl -sS "http://192.168.86.179:30185/metrics" | head -n 40
```

## Chat (auth required)

Correlation IDs: send **`X-Request-Id`** / **`X-Trace-Id`** (optional); gateway mints if omitted. Session: **`X-Session-Id`** (optional); gateway mints `sess_…` if omitted. Do **not** send `session_id`, `request_id`, or `trace_id` in the JSON body (rejected).

**Non-stream JSON**

```bash
curl -sS -X POST "http://192.168.86.179:30185/api/chat" \
  -H "Authorization: Bearer demo-token" \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: smoke-sess-001" \
  -H "X-Request-Id: smoke-req-001" \
  -H "X-Trace-Id: smoke-trace-001" \
  -d '{
    "conversation_id": "smoke-conv-001",
    "message": "Hello from smoke test",
    "metadata": { "page": "/smoke", "user_agent": "curl" }
  }' | jq .
```

Expect `200`, `status: "success"`, and echoed `request_id` / `trace_id` / `session_id` in the body.

**SSE stream** (`Accept: text/event-stream` or JSON `"stream": true`)

```bash
curl -N -sS -X POST "http://192.168.86.179:30185/api/chat" \
  -H "Authorization: Bearer demo-token" \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: smoke-sess-002" \
  -d '{
    "message": "Stream one short line",
    "stream": true,
    "metadata": { "user_agent": "curl-smoke" }
  }'
```

Expect lines starting with `event: meta`, then `event: token`, then `event: done`.

**Auth failure** (no `Authorization` header)

```bash
curl -sS -o /dev/stderr -w "%{http_code}\n" -X POST "http://192.168.86.179:30185/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message":"should fail"}'
```

Expect `401`.

## Feedback (only when `ORCHESTRATOR_CONTRACT=flat_headers`)

If the gateway is in `gateway_json` mode, `POST /api/feedback` returns **501**.

**Thumbs up**

```bash
curl -sS -X POST "http://192.168.86.179:30185/api/feedback" \
  -H "Authorization: Bearer demo-token" \
  -H "Content-Type: application/json" \
  -d '{
    "trace_id": "smoke-trace-001",
    "request_id": "smoke-req-001",
    "rating": "thumbs_up"
  }' | jq .
```

**Thumbs down** (optional fields)

```bash
curl -sS -X POST "http://192.168.86.179:30185/api/feedback" \
  -H "Authorization: Bearer demo-token" \
  -H "Content-Type: application/json" \
  -d '{
    "trace_id": "smoke-trace-001",
    "rating": "thumbs_down",
    "feedback_type": "not_factual",
    "comment": "Smoke test comment",
    "question": "Original question text"
  }' | jq .
```

Use the same `trace_id` / `request_id` you sent on the related `/api/chat` call so downstream can correlate.

## Minimal checklist

| Step | Endpoint | Expect |
|------|-----------|--------|
| 1 | `GET /health` | `200`, `"status":"ok"` |
| 2 | `GET /ready` | `200` if orchestrator healthy, else `503` |
| 3 | `GET /metrics` | `200`, body contains `gateway_requests_total` |
| 4 | `POST /api/chat` (JSON) | `200`, success payload |
| 5 | `POST /api/chat` with `"stream": true` in body | SSE `meta` → `token` → `done` |
| 6 | `POST /api/feedback` | `200`/`204`/`4xx` from upstream when `flat_headers`; else `501` |
