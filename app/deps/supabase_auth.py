"""FastAPI dependency: bearer token plus verified ``UserClaims``."""

from fastapi import Header

from app.deps.auth_bearer import parse_bearer
from app.services.auth_claims import UserClaims
from app.services.supabase_auth import verify_access_token


def token_and_claims(authorization: str | None = Header(default=None)) -> tuple[str, UserClaims]:
    """Parse Authorization header and verify access token to profile claims."""
    token = parse_bearer(authorization)
    return token, verify_access_token(token)
