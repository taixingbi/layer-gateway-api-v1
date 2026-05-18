from functools import lru_cache

from supabase import Client, create_client

from app.core.config import Settings, get_settings


@lru_cache(maxsize=1)
def get_supabase_client() -> Client | None:
    settings = get_settings()
    if not settings.supabase_enabled:
        return None
    return create_client(settings.supabase_url, settings.supabase_anon_key)


@lru_cache(maxsize=1)
def get_supabase_admin_client() -> Client | None:
    settings = get_settings()
    key = (settings.supabase_service_key or "").strip()
    if not settings.supabase_enabled or not key:
        return None
    return create_client(settings.supabase_url, key)


def require_supabase() -> Client:
    client = get_supabase_client()
    if client is None:
        raise RuntimeError(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_ANON_KEY in gateway .env"
        )
    return client
