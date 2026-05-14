from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "layer-gateway-api-v1"
    service_name: str = "gateway-api"
    env: str = "dev"

    max_inflight_requests: int = 100

    orchestrator_base_url: str = "http://192.168.86.179:30184"
    orchestrator_chat_path: str = "/v1/orchestrator/chat"
    orchestrator_feedback_path: str = "/feedback"
    orchestrator_contract: Literal["gateway_json", "flat_headers"] = "gateway_json"
    orchestrator_timeout_ms: int = 15000
    orchestrator_retry_max_attempts: int = 2
    orchestrator_readiness_path: str = "/health"
    orchestrator_readiness_timeout_ms: int = 3000
    orchestrator_readiness_probe_enabled: bool = True

    auth_mode: Literal["stub", "jwt"] = "stub"
    auth_stub_user_id: str = "user_001"
    auth_stub_tenant_id: str = "tenant_01"
    auth_stub_roles: str = "customer"
    auth_stub_groups: str = ""
    auth_stub_teams: str = ""

    auth_jwt_issuer: str = ""
    auth_jwt_audience: str = ""
    auth_jwt_jwks_url: str = ""
    auth_jwt_algorithms: str = "RS256,ES256"
    auth_jwt_claim_user_id: str = "sub"
    auth_jwt_claim_tenant_id: str = "tenant_id"
    auth_jwt_default_tenant_id: str = ""
    auth_jwt_claim_roles: str = "roles"
    auth_jwt_claim_groups: str = "groups"
    auth_jwt_claim_teams: str = "teams"

    chat_message_max_length: int = 4000

    # Timeout hierarchy (document / future wiring): outer > inner. Only ``orchestrator_timeout_ms`` drives httpx today.
    client_timeout_ms: int = 65000
    gateway_timeout_ms: int = 60000

    @field_validator("auth_mode", mode="before")
    @classmethod
    def _normalize_auth_mode(cls, v: str) -> str:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @model_validator(mode="after")
    def _validate_jwt_auth_config(self) -> "Settings":
        if self.auth_mode != "jwt":
            return self
        missing: list[str] = []
        if not (self.auth_jwt_issuer or "").strip():
            missing.append("AUTH_JWT_ISSUER")
        if not (self.auth_jwt_audience or "").strip():
            missing.append("AUTH_JWT_AUDIENCE")
        if not (self.auth_jwt_jwks_url or "").strip():
            missing.append("AUTH_JWT_JWKS_URL")
        algs = [a.strip() for a in self.auth_jwt_algorithms.split(",") if a.strip()]
        if not algs:
            missing.append("AUTH_JWT_ALGORITHMS (non-empty)")
        if missing:
            raise ValueError(
                "When AUTH_MODE=jwt, the following settings are required: " + ", ".join(missing)
            )
        return self

    def jwt_algorithm_list(self) -> list[str]:
        return [a.strip() for a in self.auth_jwt_algorithms.split(",") if a.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
