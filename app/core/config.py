"""Environment-backed gateway settings (Pydantic ``BaseSettings``)."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Gateway configuration loaded from environment and optional ``.env`` file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "layer-gateway-api-v1"
    service_name: str = "gateway-api"
    env: str = "dev"

    max_inflight_requests: int = 16
    max_concurrent_chat_streams: int = 8
    max_concurrent_streams_per_user: int = 2
    rate_limit_chat_requests_per_min: int = 6
    rate_limit_chat_burst: int = 2

    orchestrator_base_url: str = "http://192.168.86.179:30184"
    orchestrator_chat_path: str = "/v1/orchestrator/answer"
    orchestrator_feedback_path: str = "/feedback"
    orchestrator_contract: Literal["gateway_json", "flat_headers"] = "gateway_json"
    orchestrator_timeout_ms: int = 120000
    orchestrator_retry_max_attempts: int = 2
    orchestrator_readiness_path: str = "/health"
    orchestrator_readiness_timeout_ms: int = 3000
    orchestrator_readiness_probe_enabled: bool = True

    auth_mode: Literal["jwt"] = "jwt"
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

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY"),
    )
    frontend_url: str = "http://localhost:3000"
    additional_frontend_urls: str = ""
    jwt_expiry_seconds: int = 3600

    # Guest /chat: BFF sends a shared service bearer; identity is fixed ``anyuser`` (public RAG only).
    guest_chat_enabled: bool = False
    guest_chat_service_token: str = ""
    guest_chat_user_id: str = "guest"

    chat_message_max_length: int = 4000
    chat_assistant_model: str = ""
    # Set true only after ``alter table messages add column status text`` in Supabase.
    chat_persist_message_status: bool = False

    @property
    def supabase_enabled(self) -> bool:
        """True when Supabase URL and anon key are both configured."""
        return bool((self.supabase_url or "").strip() and (self.supabase_anon_key or "").strip())

    # Timeout hierarchy (document / future wiring): outer > inner. Only ``orchestrator_timeout_ms`` drives httpx today.
    client_timeout_ms: int = 65000
    gateway_timeout_ms: int = 60000

    @field_validator("auth_mode", mode="before")
    @classmethod
    def _normalize_auth_mode(cls, v: str) -> str:
        """Lowercase and strip ``auth_mode`` from env."""
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @model_validator(mode="after")
    def _validate_auth_config(self) -> "Settings":
        """Require JWT issuer/audience/JWKS when Supabase is not enabled."""
        if self.supabase_enabled:
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
                "When Supabase is not configured, AUTH_JWT_* is required: " + ", ".join(missing)
            )
        return self

    def jwt_algorithm_list(self) -> list[str]:
        """Parse comma-separated ``AUTH_JWT_ALGORITHMS`` into a list."""
        return [a.strip() for a in self.auth_jwt_algorithms.split(",") if a.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached gateway settings singleton."""
    return Settings()
