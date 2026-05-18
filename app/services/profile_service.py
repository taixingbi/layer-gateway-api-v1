import re
import uuid

from fastapi import HTTPException
from postgrest.exceptions import APIError
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.services.auth_claims import UserClaims
from app.services.auth_roles import normalize_roles
from app.services.supabase_client import get_supabase_admin_client, require_supabase
from app.services.time_util import format_iso_est
from app.services.user_meta import meta_get, sync_jwt_metadata

DEFAULT_TEAM = "ai-platform"
DEFAULT_GROUP = "engineering"
DEFAULT_PLAN = "free"

PROFILE_COLUMNS = (
    "id,email,username,display_name,roles,plan,team,user_group,created_at,updated_at"
)


class ProfileUpdate(BaseModel):
    email: EmailStr | None = None
    username: str | None = Field(default=None, min_length=1, max_length=64)
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    roles: list[str] | None = None
    team: str | None = Field(default=None, min_length=1, max_length=64)
    group: str | None = Field(default=None, min_length=1, max_length=64)
    plan: str | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("roles")
    @classmethod
    def roles_not_empty(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and not any(str(r).strip() for r in value):
            raise ValueError("roles must not be empty")
        return value


def _default_username(email: str | None, user_id: str) -> str:
    if email and "@" in email:
        local = email.split("@", 1)[0].lower()
        base = re.sub(r"[^a-z0-9_]", "_", local).strip("_")[:48]
        if base:
            return base
    return f"user_{user_id.replace('-', '')[:12]}"


def _roles_from_user(user, row: dict) -> list[str]:
    if row.get("roles") is not None:
        return normalize_roles(row.get("roles"))
    for source in (user.user_metadata or {}, user.app_metadata or {}):
        if source.get("roles") is not None:
            return normalize_roles(source.get("roles"))
        legacy = source.get("role")
        if isinstance(legacy, str) and legacy.strip():
            return [legacy.strip()]
    return ["user"]


def user_to_claims(user, profile_row: dict | None = None) -> UserClaims:
    row = profile_row or {}
    return UserClaims(
        user_id=user.id,
        email=row.get("email") or user.email,
        roles=_roles_from_user(user, row),
        team=row.get("team") or meta_get(user, "team", DEFAULT_TEAM),
        group=row.get("user_group") or meta_get(user, "group", DEFAULT_GROUP),
        plan=row.get("plan") or meta_get(user, "plan", DEFAULT_PLAN),
    )


def _claims_from_row(row: dict) -> UserClaims:
    return UserClaims(
        user_id=row["id"],
        email=row.get("email"),
        roles=normalize_roles(row.get("roles")),
        team=row.get("team") or DEFAULT_TEAM,
        group=row.get("user_group") or DEFAULT_GROUP,
        plan=row.get("plan") or DEFAULT_PLAN,
    )


def _row_to_dict(row: dict) -> dict:
    claims = _claims_from_row(row)
    return {
        "id": row["id"],
        "email": row.get("email"),
        "username": row.get("username"),
        "display_name": row.get("display_name"),
        "roles": claims.roles,
        "team": claims.team,
        "group": claims.group,
        "plan": claims.plan,
        "jwt_claims": claims.to_jwt_claims(),
        "created_at": format_iso_est(row.get("created_at")),
        "updated_at": format_iso_est(row.get("updated_at")),
    }


def _handle_db_error(exc: Exception) -> None:
    if isinstance(exc, APIError):
        message = exc.message or str(exc)
        if "schema cache" in message.lower() or "could not find" in message.lower():
            raise HTTPException(
                status_code=400,
                detail=f"profiles column mismatch: {message}",
            ) from exc
        if exc.code == "42501" or "row-level security" in message.lower():
            raise HTTPException(
                status_code=403,
                detail=(
                    "Profile blocked by Supabase RLS. Add SUPABASE_SERVICE_KEY "
                    "to gateway .env, or run sql/profiles_rls.sql in Supabase."
                ),
            ) from exc
        if "duplicate" in message.lower() or "unique" in message.lower():
            raise HTTPException(status_code=409, detail="Username already taken") from exc
        raise HTTPException(status_code=400, detail=message) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


def resolve_login_email(identifier: str) -> str:
    raw = identifier.strip()
    if not raw:
        raise HTTPException(status_code=401, detail="Invalid login credentials")
    if "@" in raw:
        return raw

    admin = get_supabase_admin_client()
    if not admin:
        raise HTTPException(
            status_code=503,
            detail="Username login requires SUPABASE_SERVICE_KEY in gateway .env",
        )
    try:
        result = admin.table("profiles").select("email").eq("username", raw).limit(1).execute()
    except Exception as exc:
        _handle_db_error(exc)
    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid login credentials")
    email = result.data[0].get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Invalid login credentials")
    return email


def _profiles_table(access_token: str):
    admin = get_supabase_admin_client()
    if admin:
        return admin.table("profiles")
    supabase = require_supabase()
    supabase.postgrest.auth(access_token)
    return supabase.table("profiles")


def fetch_profile_row(access_token: str, user_id: str) -> dict | None:
    try:
        result = _profiles_table(access_token).select(PROFILE_COLUMNS).eq("id", user_id).execute()
    except Exception as exc:
        _handle_db_error(exc)
    if not result.data:
        return None
    return result.data[0]


def ensure_profile(access_token: str, claims: UserClaims) -> dict:
    row = fetch_profile_row(access_token, claims.user_id)
    if row:
        return _row_to_dict(row)

    base_username = _default_username(claims.email, claims.user_id)
    insert = {
        "id": claims.user_id,
        "email": claims.email,
        "username": base_username,
        "roles": claims.roles,
        "team": claims.team,
        "user_group": claims.group,
        "plan": claims.plan,
    }
    created = None
    last_exc: Exception | None = None
    for attempt in range(3):
        if attempt > 0:
            insert["username"] = f"{base_username}_{uuid.uuid4().hex[:6]}"
        try:
            created = _profiles_table(access_token).insert(insert).execute()
            break
        except Exception as exc:
            last_exc = exc
            message = str(exc).lower()
            if attempt < 2 and ("duplicate" in message or "unique" in message or "23505" in message):
                continue
            _handle_db_error(exc)

    if created is None and last_exc is not None:
        _handle_db_error(last_exc)
    if not created or not created.data:
        row = fetch_profile_row(access_token, claims.user_id)
        if row:
            return _row_to_dict(row)
        raise HTTPException(status_code=500, detail="Failed to create profile")

    row = created.data[0]
    db_claims = _claims_from_row(row)
    sync_jwt_metadata(claims.user_id, access_token, team=db_claims.team, group=db_claims.group, plan=db_claims.plan, roles=db_claims.roles)
    return _row_to_dict(row)


def get_profile(access_token: str, claims: UserClaims) -> dict:
    row = fetch_profile_row(access_token, claims.user_id)
    if row:
        return _row_to_dict(row)
    return ensure_profile(access_token, claims)


def update_profile(access_token: str, claims: UserClaims, body: ProfileUpdate) -> dict:
    ensure_profile(access_token, claims)
    updates: dict = {}
    if body.email is not None:
        updates["email"] = body.email
    if body.username is not None:
        updates["username"] = body.username.strip()
    if body.display_name is not None:
        updates["display_name"] = body.display_name.strip()
    if body.roles is not None:
        updates["roles"] = normalize_roles(body.roles)
    if body.team is not None:
        updates["team"] = body.team.strip()
    if body.group is not None:
        updates["user_group"] = body.group.strip()
    if body.plan is not None:
        updates["plan"] = body.plan.strip()

    if not updates:
        return get_profile(access_token, claims)

    try:
        result = _profiles_table(access_token).update(updates).eq("id", claims.user_id).execute()
    except Exception as exc:
        _handle_db_error(exc)

    if not result.data:
        raise HTTPException(status_code=404, detail="Profile not found")

    row = fetch_profile_row(access_token, claims.user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")

    db_claims = _claims_from_row(row)
    sync_jwt_metadata(
        claims.user_id,
        access_token,
        team=db_claims.team,
        group=db_claims.group,
        plan=db_claims.plan,
        roles=db_claims.roles,
    )
    return _row_to_dict(row)
