from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "layer-gateway-api-v1"
    env: str = "dev"

    orchestrator_base_url: str = "http://192.168.86.179:30184"
    orchestrator_chat_path: str = "/v1/orchestrator/chat"
    orchestrator_feedback_path: str = "/feedback"
    orchestrator_contract: Literal["gateway_json", "flat_headers"] = "gateway_json"
    orchestrator_timeout_ms: int = 15000
    orchestrator_retry_max_attempts: int = 2

    auth_mode: str = "stub"
    auth_stub_user_id: str = "user_001"
    auth_stub_tenant_id: str = "tenant_01"
    auth_stub_roles: str = "customer"
    auth_stub_groups: str = ""
    auth_stub_teams: str = ""
    chat_message_max_length: int = 4000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
