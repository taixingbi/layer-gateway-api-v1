import time

import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.core.config import Settings, get_settings
from app.main import create_app
from app.services.jwt_validator import JwtValidator, JwtVerifyError, claims_to_auth_context


class _StubOrch:
    async def chat(self, payload, ctx=None):
        return type(
            "R",
            (),
            {"answer": "ok", "citations": [], "usage": {"input_tokens": 1, "output_tokens": 2}},
        )()

    async def stream_chat(self, payload, ctx=None):
        yield 'event: token\ndata: {"text":"x"}\n\n'


class _StaticJwkClient:
    def __init__(self, signing_key):
        self._signing_key = signing_key

    def get_signing_key_from_jwt(self, token: str):
        return self._signing_key


def _rsa_keypair_and_jwk():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    public_key = private_key.public_key()
    jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk["kid"] = "test-kid"
    jwk["use"] = "sig"
    signing_key = jwt.PyJWK.from_dict(jwk)
    return private_key, signing_key


def test_claims_to_auth_context_maps_standard_claims():
    settings = Settings()
    claims = {
        "sub": "user-1",
        "tenant_id": "ten-a",
        "roles": ["hr", "admin"],
        "groups": "g1, g2",
        "teams": ["t1"],
    }
    ctx = claims_to_auth_context(claims, settings)
    assert ctx["user_id"] == "user-1"
    assert ctx["tenant_id"] == "ten-a"
    assert ctx["roles"] == ["hr", "admin"]
    assert ctx["groups"] == ["g1", "g2"]
    assert ctx["teams"] == ["t1"]


def test_claims_to_auth_context_default_roles_and_tenant():
    settings = Settings(auth_jwt_default_tenant_id="fallback-tenant")
    claims = {"sub": "u2", "exp": 9999999999}
    ctx = claims_to_auth_context(claims, settings)
    assert ctx["user_id"] == "u2"
    assert ctx["tenant_id"] == "fallback-tenant"
    assert ctx["roles"] == ["customer"]
    assert ctx["groups"] == []
    assert ctx["teams"] == []


def test_claims_to_auth_context_missing_sub_raises():
    settings = Settings()
    with pytest.raises(JwtVerifyError):
        claims_to_auth_context({"tenant_id": "x"}, settings)


def test_settings_jwt_mode_requires_issuer_audience_jwks():
    with pytest.raises(ValueError, match="AUTH_JWT"):
        Settings(
            auth_mode="jwt",
            auth_jwt_issuer="",
            auth_jwt_audience="aud",
            auth_jwt_jwks_url="https://x/jwks",
        )


@pytest.fixture
def jwt_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "")
    monkeypatch.setenv("AUTH_MODE", "jwt")
    monkeypatch.setenv("AUTH_JWT_ISSUER", "https://issuer.test/")
    monkeypatch.setenv("AUTH_JWT_AUDIENCE", "my-api")
    monkeypatch.setenv("AUTH_JWT_JWKS_URL", "http://127.0.0.1:9/unused-jwks")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _mint_token(private_key, **extra) -> str:
    now = int(time.time())
    payload = {
        "iss": "https://issuer.test/",
        "aud": "my-api",
        "sub": "jwt-user",
        "exp": now + 3600,
        "tenant_id": "ten-jwt",
        "roles": ["operator"],
        **extra,
    }
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-kid"})


def test_chat_accepts_valid_jwt(jwt_env):
    private_key, py_jwk = _rsa_keypair_and_jwk()
    token = _mint_token(private_key)
    app = create_app()
    settings = get_settings()
    with TestClient(app) as client:
        client.app.state.jwt_validator = JwtValidator(settings, jwk_client=_StaticJwkClient(py_jwk))
        client.app.state.orchestrator_client = _StubOrch()
        response = client.post(
            "/api/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "hello", "metadata": {}},
        )
    assert response.status_code == 200


def test_chat_rejects_expired_jwt(jwt_env):
    private_key, py_jwk = _rsa_keypair_and_jwk()
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "https://issuer.test/",
            "aud": "my-api",
            "sub": "jwt-user",
            "exp": now - 10,
            "tenant_id": "t",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )
    app = create_app()
    settings = get_settings()
    with TestClient(app) as client:
        client.app.state.jwt_validator = JwtValidator(settings, jwk_client=_StaticJwkClient(py_jwk))
        response = client.post(
            "/api/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "hello", "metadata": {}},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_chat_rejects_wrong_audience_jwt(jwt_env):
    private_key, py_jwk = _rsa_keypair_and_jwk()
    token = _mint_token(private_key, aud="other-api")
    app = create_app()
    settings = get_settings()
    with TestClient(app) as client:
        client.app.state.jwt_validator = JwtValidator(settings, jwk_client=_StaticJwkClient(py_jwk))
        response = client.post(
            "/api/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "hello", "metadata": {}},
        )
    assert response.status_code == 401


def test_jwt_validator_accepts_multi_audience_setting(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "jwt")
    monkeypatch.setenv("AUTH_JWT_ISSUER", "https://issuer.test/")
    monkeypatch.setenv("AUTH_JWT_AUDIENCE", "api-a, api-b")
    monkeypatch.setenv("AUTH_JWT_JWKS_URL", "http://127.0.0.1:9/jwks")
    get_settings.cache_clear()
    private_key, py_jwk = _rsa_keypair_and_jwk()
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "https://issuer.test/",
            "aud": "api-b",
            "sub": "u",
            "exp": now + 3600,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )
    settings = get_settings()
    v = JwtValidator(settings, jwk_client=_StaticJwkClient(py_jwk))
    claims = v.verify(token)
    assert claims["sub"] == "u"
    get_settings.cache_clear()
