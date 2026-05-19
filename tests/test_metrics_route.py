"""Prometheus ``/metrics`` scrape endpoint tests."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_metrics_endpoint_unauthenticated():
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert b"gateway_requests_total" in response.content
