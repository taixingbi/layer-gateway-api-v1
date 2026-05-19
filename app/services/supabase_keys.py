"""Helpers for inspecting Supabase API key JWT payloads."""

import base64
import json


def jwt_role(key: str) -> str | None:
    """Best-effort decode of Supabase JWT ``role`` claim (anon vs service_role)."""
    raw = (key or "").strip()
    if not raw or raw.count(".") < 2:
        return None
    try:
        payload = raw.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        role = data.get("role")
        return role if isinstance(role, str) else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
