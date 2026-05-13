"""Prometheus metrics for the gateway (see README for scrape setup)."""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

REQUESTS_TOTAL = Counter(
    "gateway_requests_total",
    "Total HTTP requests handled by the gateway",
    ["method", "path", "status"],
)

REJECTED_TOTAL = Counter(
    "gateway_rejected_requests_total",
    "Requests rejected before reaching route handlers",
    ["reason"],
)

LATENCY_MS = Histogram(
    "gateway_request_latency_ms",
    "End-to-end request latency in milliseconds",
    ["method", "path"],
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 120000),
)

TTFB_MS = Histogram(
    "gateway_ttfb_ms",
    "Time to first byte for streaming responses in milliseconds",
    ["method", "path"],
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 120000),
)

INFLIGHT = Gauge(
    "gateway_inflight_requests",
    "Number of requests currently inside the gateway (after inflight gate)",
)
