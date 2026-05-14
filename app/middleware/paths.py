"""Path sets shared by middleware (auth, inflight, and future edge behavior)."""

# Liveness, readiness, and Prometheus: no bearer auth, no inflight slot; callers must
# not be required to send X-Request-Id / X-Trace-Id / X-Session-Id / X-Conversation-Id.
PUBLIC_PROBE_PATHS: frozenset[str] = frozenset({"/health", "/ready", "/metrics"})
