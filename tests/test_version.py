"""GET /version build metadata."""

import os

# Before app imports (see tests/conftest.py).
os.environ.setdefault("AUTH_MODE", "jwt")
os.environ.setdefault("AUTH_JWT_ISSUER", "test-issuer")
os.environ.setdefault("AUTH_JWT_AUDIENCE", "test-audience")
os.environ.setdefault("AUTH_JWT_JWKS_URL", "http://localhost/.well-known/jwks.json")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")

from fastapi.testclient import TestClient

from app.build_info import SERVICE_NAME, version_payload
from app.main import create_app


def test_version_payload_from_env(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v1.0.0")
    monkeypatch.setenv("GIT_SHA", "abc1234")
    monkeypatch.setenv("GIT_BRANCH", "main")
    monkeypatch.setenv("BUILD_TIME", "2026-06-01T12:30:00Z")
    monkeypatch.setenv("BUILD_IMAGE", "ghcr.io/taixingbi/layer-gateway-api-v1:v1.0.0")
    monkeypatch.setenv("IMAGE_DIGEST", "sha256:deadbeef")
    monkeypatch.setenv("ENVIRONMENT", "ai-dev")
    assert version_payload() == {
        "service": SERVICE_NAME,
        "version": "v1.0.0",
        "git_sha": "abc1234",
        "git_branch": "main",
        "build_time": "2026-06-01T12:30:00Z",
        "image": "ghcr.io/taixingbi/layer-gateway-api-v1:v1.0.0",
        "image_digest": "sha256:deadbeef",
        "environment": "ai-dev",
        "status": "ok",
    }


def test_version_endpoint_no_auth(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v1.0.0")
    monkeypatch.setenv("ENVIRONMENT", "ai-dev")
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/version")
    assert response.status_code == 200
    assert response.json()["service"] == SERVICE_NAME
    assert response.json()["status"] == "ok"
