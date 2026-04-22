from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "layer-gateway-api-v1"
    env: str = "dev"

    orchestrator_base_url: str = "http://localhost:8080"
    orchestrator_chat_path: str = "/v1/orchestrator/chat"
    orchestrator_timeout_ms: int = 15000
    orchestrator_retry_max_attempts: int = 2

    auth_mode: str = "stub"
    auth_stub_user_id: str = "user_001"
    auth_stub_tenant_id: str = "tenant_01"
    chat_message_max_length: int = 4000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
