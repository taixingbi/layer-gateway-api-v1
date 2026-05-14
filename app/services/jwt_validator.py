from __future__ import annotations

from typing import Any, Protocol

import jwt
from jwt import PyJWKClient

from app.core.config import Settings


class SigningKeyProvider(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


class JwtVerifyError(Exception):
    """Raised when a bearer token fails JWT verification."""


def _split_csv(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def _audiences_from_settings(settings: Settings) -> str | list[str]:
    parts = _split_csv(settings.auth_jwt_audience)
    if not parts:
        raise JwtVerifyError("invalid auth configuration")
    if len(parts) == 1:
        return parts[0]
    return parts


def _claim_to_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for x in value:
            if x is None:
                continue
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    if isinstance(value, str):
        return _split_csv(value)
    s = str(value).strip()
    return [s] if s else []


def _get_scalar_claim(claims: dict[str, Any], claim_name: str) -> str:
    if not claim_name:
        return ""
    raw = claims.get(claim_name)
    if raw is None:
        return ""
    return str(raw).strip()


def claims_to_auth_context(claims: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Map verified JWT claims to gateway ``auth_context`` (trusted, not from client body)."""
    user_id = _get_scalar_claim(claims, settings.auth_jwt_claim_user_id)
    if not user_id:
        raise JwtVerifyError("token missing user id claim")

    tenant = _get_scalar_claim(claims, settings.auth_jwt_claim_tenant_id)
    if not tenant:
        tenant = (settings.auth_jwt_default_tenant_id or "").strip()

    roles = _claim_to_str_list(claims.get(settings.auth_jwt_claim_roles))
    if not roles:
        roles = ["customer"]

    groups = _claim_to_str_list(claims.get(settings.auth_jwt_claim_groups))
    teams = _claim_to_str_list(claims.get(settings.auth_jwt_claim_teams))

    return {
        "user_id": user_id,
        "tenant_id": tenant,
        "roles": roles,
        "groups": groups,
        "teams": teams,
    }


class JwtValidator:
    """OIDC-style access token verification using JWKS (production)."""

    def __init__(self, settings: Settings, jwk_client: SigningKeyProvider | None = None):
        self._settings = settings
        self._jwk_client: SigningKeyProvider = jwk_client or PyJWKClient(settings.auth_jwt_jwks_url)

    def verify(self, token: str) -> dict[str, Any]:
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=self._settings.jwt_algorithm_list(),
                audience=_audiences_from_settings(self._settings),
                issuer=(self._settings.auth_jwt_issuer or "").strip(),
                options={
                    "require": ["exp", "iss"],
                    "verify_aud": True,
                },
            )
        except jwt.PyJWTError as e:
            raise JwtVerifyError("invalid token") from e
        if not isinstance(payload, dict):
            raise JwtVerifyError("invalid token")
        return payload

    def verify_to_auth_context(self, token: str) -> dict[str, Any]:
        claims = self.verify(token)
        return claims_to_auth_context(claims, self._settings)
