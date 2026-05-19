"""Supabase Python client factories (anon and service role)."""

from functools import lru_cache

from supabase import Client, create_client

from app.core.config import Settings, get_settings
from app.services.supabase_keys import jwt_role


@lru_cache(maxsize=1)
def get_supabase_client() -> Client | None:
    """Return anon-key Supabase client or None if not configured."""
    settings = get_settings()
    if not settings.supabase_enabled:
        return None
    return create_client(settings.supabase_url, settings.supabase_anon_key)


@lru_cache(maxsize=1)
def get_supabase_admin_client() -> Client | None:
    """Return service-role Supabase client or None if key missing."""
    settings = get_settings()
    key = (settings.supabase_service_key or "").strip()
    if not settings.supabase_enabled or not key:
        return None
    return create_client(settings.supabase_url, key)


def admin_client_configured() -> bool:
    """True when Supabase is enabled and service key is set."""
    settings = get_settings()
    return bool(settings.supabase_enabled and (settings.supabase_service_key or "").strip())


def service_key_role() -> str | None:
    """Decode ``role`` claim from configured service key JWT."""
    settings = get_settings()
    return jwt_role(settings.supabase_service_key)


def require_supabase() -> Client:
    """Return anon client or raise if Supabase env is missing."""
    client = get_supabase_client()
    if client is None:
        raise RuntimeError(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_ANON_KEY in gateway .env"
        )
    return client
