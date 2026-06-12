"""Guest (unauthenticated) chat bearer for public /chat when enabled."""

from __future__ import annotations

import secrets
from typing import Any

from app.core.config import Settings

_GUEST_ROLE = "anyuser"


def guest_chat_auth_context(settings: Settings) -> dict[str, Any]:
    """Trusted auth context for guest chat (public RAG only via ``anyuser`` role)."""
    tenant = (settings.auth_jwt_default_tenant_id or "").strip()
    user_id = (settings.guest_chat_user_id or "guest").strip() or "guest"
    return {
        "user_id": user_id,
        "tenant_id": tenant,
        "roles": [_GUEST_ROLE],
        "groups": [],
        "teams": [],
        "guest": True,
    }


def try_guest_chat_auth(token: str, settings: Settings) -> dict[str, Any] | None:
    """Return guest auth context when guest chat is enabled and the bearer matches."""
    if not settings.guest_chat_enabled:
        return None
    expected = (settings.guest_chat_service_token or "").strip()
    if not expected:
        return None
    if not secrets.compare_digest(token, expected):
        return None
    return guest_chat_auth_context(settings)
