"""Mirror profile claims into auth.users user_metadata."""

import base64
import json

import httpx
from fastapi import HTTPException
from supabase_auth.errors import AuthApiError

from app.core.config import get_settings
from app.services.supabase_client import get_supabase_admin_client, get_supabase_client, require_supabase


def meta_get(user, key: str, default: str) -> str:
    for source in (user.user_metadata or {}, user.app_metadata or {}):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _jwt_role(key: str | None) -> str | None:
    if not key or not key.startswith("eyJ"):
        return None
    try:
        parts = key.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        role = payload.get("role")
        return role if isinstance(role, str) else None
    except Exception:
        return None


def _service_role_client():
    settings = get_settings()
    admin = get_supabase_admin_client()
    if not admin or not (settings.supabase_service_key or "").strip():
        return None
    if _jwt_role(settings.supabase_service_key) != "service_role":
        return None
    return admin


def _update_user_metadata(access_token: str, data: dict) -> None:
    settings = get_settings()
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/user"
    try:
        res = httpx.put(
            url,
            headers={
                "apikey": settings.supabase_anon_key,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"data": data},
            timeout=30.0,
        )
        res.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or str(exc)
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def sync_jwt_metadata(
    user_id: str,
    access_token: str,
    *,
    team: str | None = None,
    group: str | None = None,
    plan: str | None = None,
    roles: list[str] | None = None,
) -> None:
    payload: dict = {}
    if team is not None:
        payload["team"] = team
    if group is not None:
        payload["group"] = group
    if plan is not None:
        payload["plan"] = plan
    if roles is not None:
        payload["roles"] = roles
    if not payload:
        return

    admin = _service_role_client()
    if admin:
        try:
            admin.auth.admin.update_user_by_id(user_id, {"user_metadata": payload})
            return
        except AuthApiError as exc:
            if "not allowed" not in str(exc).lower():
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    supabase = require_supabase()
    try:
        current = supabase.auth.get_user(access_token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    if not current or not current.user or current.user.id != user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    merged = dict(current.user.user_metadata or {})
    merged.update(payload)

    try:
        _update_user_metadata(access_token, merged)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{exc.detail}. "
                    "Set SUPABASE_SERVICE_KEY to the real service_role secret, not the anon key."
                ),
            ) from exc
        raise
