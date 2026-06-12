"""Chat rate and concurrency limits."""

import time

import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.core.config import Settings, get_settings
from app.main import create_app
from app.middleware.chat_limits import (
    _acquire_chat_slot,
    _release_chat_slot,
    reset_chat_limits_for_tests,
)
from app.services.jwt_validator import JwtValidator
from app.services.token_bucket import TokenBucket


def _rsa_keypair_and_jwk():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    public_key = private_key.public_key()
    jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk["kid"] = "test-kid"
    jwk["use"] = "sig"
    signing_key = jwt.PyJWK.from_dict(jwk)
    return private_key, signing_key


class _StaticJwkClient:
    def __init__(self, signing_key):
        self._signing_key = signing_key

    def get_signing_key_from_jwt(self, token: str):
        return self._signing_key


@pytest.fixture
def jwt_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "")
    monkeypatch.setenv("AUTH_MODE", "jwt")
    monkeypatch.setenv("AUTH_JWT_ISSUER", "https://issuer.test/")
    monkeypatch.setenv("AUTH_JWT_AUDIENCE", "my-api")
    monkeypatch.setenv("AUTH_JWT_JWKS_URL", "http://127.0.0.1:9/unused-jwks")
    get_settings.cache_clear()
    reset_chat_limits_for_tests()
    yield
    get_settings.cache_clear()
    reset_chat_limits_for_tests()


def _chat_client(jwt_env, monkeypatch, **settings_overrides):
    private_key, py_jwk = _rsa_keypair_and_jwk()
    for key, value in settings_overrides.items():
        env_key = key.upper()
        monkeypatch.setenv(env_key, str(value))
    get_settings.cache_clear()
    reset_chat_limits_for_tests()
    app = create_app()
    settings = get_settings()
    token = jwt.encode(
        {
            "iss": "https://issuer.test/",
            "aud": "my-api",
            "sub": "user-a",
            "exp": int(time.time()) + 3600,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )
    orch = object()
    validator = JwtValidator(settings, jwk_client=_StaticJwkClient(py_jwk))

    def _wire(client: TestClient) -> None:
        client.app.state.jwt_validator = validator
        client.app.state.orchestrator_client = orch

    client = TestClient(app)
    _wire(client)
    return app, client, token, _wire


def test_token_bucket_burst_then_reject():
    bucket = TokenBucket(rate_per_minute=60, burst=2)
    assert bucket.consume()
    assert bucket.consume()
    assert not bucket.consume()


@pytest.mark.asyncio
async def test_per_user_concurrent_slot_limit():
    reset_chat_limits_for_tests()
    settings = Settings(
        max_concurrent_streams_per_user=1,
        max_concurrent_chat_streams=10,
        rate_limit_chat_requests_per_min=0,
    )
    assert await _acquire_chat_slot("user-a", settings) is None
    blocked = await _acquire_chat_slot("user-a", settings)
    assert blocked is not None
    assert blocked.status_code == 429
    await _release_chat_slot("user-a")
    assert await _acquire_chat_slot("user-a", settings) is None
    await _release_chat_slot("user-a")


def test_chat_rpm_limit(jwt_env, monkeypatch):
    _app, client, token, _wire = _chat_client(
        jwt_env,
        monkeypatch,
        max_concurrent_streams_per_user=0,
        max_concurrent_chat_streams=0,
        rate_limit_chat_requests_per_min=2,
        rate_limit_chat_burst=2,
    )

    class _StubOrch:
        async def chat(self, payload, ctx=None):
            return type("R", (), {"answer": "ok", "citations": [], "usage": {}})()

        async def stream_chat(self, payload, ctx=None):
            yield 'event: answer_delta\ndata: {"text":"x"}\n\n'

    headers = {"Authorization": f"Bearer {token}"}
    body = {"message": "hello", "stream": False, "metadata": {}}

    with client:
        _wire(client)
        client.app.state.orchestrator_client = _StubOrch()
        assert client.post("/v1/chat", headers=headers, json=body).status_code == 200
        assert client.post("/v1/chat", headers=headers, json=body).status_code == 200
        r3 = client.post("/v1/chat", headers=headers, json=body)
    assert r3.status_code == 429
    assert "Retry-After" in r3.headers
