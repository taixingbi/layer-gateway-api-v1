"""Health and readiness probe route tests."""

from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from app.services.orchestrator_client import OrchestratorClient


def test_health_ok_without_auth():
    """Health ok without auth."""
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_ok_without_auth_when_upstream_healthy():
    """Ready ok without auth when upstream healthy."""
    import asyncio

    import httpx

    async def handler(request: httpx.Request) -> httpx.Response:
        """Handler."""
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    hc = httpx.AsyncClient(base_url="http://orch", transport=transport)
    try:
        app = create_app()
        with TestClient(app) as client:
            app.state.orchestrator_client = OrchestratorClient(
                client=hc,
                settings=Settings(
                    orchestrator_base_url="http://orch",
                    orchestrator_readiness_path="/health",
                    orchestrator_readiness_timeout_ms=3000,
                ),
            )
            response = client.get("/ready")
    finally:
        asyncio.run(hc.aclose())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["orchestrator"] == "ok"


def test_ready_503_when_upstream_unhealthy():
    """Ready 503 when upstream unhealthy."""
    import asyncio

    import httpx

    async def handler(request: httpx.Request) -> httpx.Response:
        """Handler."""
        return httpx.Response(503, json={"error": "down"})

    transport = httpx.MockTransport(handler)
    hc = httpx.AsyncClient(base_url="http://orch", transport=transport)
    try:
        app = create_app()
        with TestClient(app) as client:
            app.state.orchestrator_client = OrchestratorClient(
                client=hc,
                settings=Settings(
                    orchestrator_base_url="http://orch",
                    orchestrator_readiness_path="/health",
                ),
            )
            response = client.get("/ready")
    finally:
        asyncio.run(hc.aclose())

    assert response.status_code == 503


def test_ready_probe_disabled_returns_200(monkeypatch):
    """Ready probe disabled returns 200."""
    monkeypatch.setenv("ORCHESTRATOR_READINESS_PROBE_ENABLED", "false")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/ready")
    get_settings.cache_clear()
    assert response.status_code == 200
    assert response.json()["orchestrator"] == "probe_disabled"
