"""Shared bearer token parsing for route dependencies."""

from fastapi import HTTPException


def parse_bearer(authorization: str | None) -> str:
    """Extract JWT from ``Authorization: Bearer`` header or raise 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty bearer token")
    return token
