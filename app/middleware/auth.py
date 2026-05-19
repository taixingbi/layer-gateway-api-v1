"""Bearer authentication middleware (Supabase or JWKS JWT)."""

import asyncio

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.middleware.paths import PUBLIC_AUTH_PATHS, PUBLIC_PROBE_PATHS
from app.services.jwt_validator import JwtVerifyError
from app.services.supabase_auth import SupabaseAuthError, verify_access_token_to_auth_context


def _unauthorized(message: str = "Invalid or expired bearer token") -> JSONResponse:
    """Return a 401 JSON error envelope."""
    return JSONResponse(
        status_code=401,
        content={"status": "error", "error": {"code": "unauthorized", "message": message}},
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """Authenticate incoming requests and attach trusted auth context."""

    async def dispatch(self, request: Request, call_next) -> Response:
        """Validate bearer token and set ``request.state.auth_context``."""
        # Keep health checks unauthenticated for probes and uptime monitors.
        if request.url.path in PUBLIC_PROBE_PATHS or request.url.path in PUBLIC_AUTH_PATHS:
            return await call_next(request)

        # Require bearer token on all gateway business endpoints.
        authorization = request.headers.get("authorization")
        if not authorization or not authorization.lower().startswith("bearer "):
            return JSONResponse(
                status_code=401,
                content={"status": "error", "error": {"code": "unauthorized", "message": "Missing bearer token"}},
            )

        # Parse and validate token shape before any downstream logic runs.
        token = authorization.split(" ", maxsplit=1)[1].strip()
        if not token:
            return JSONResponse(
                status_code=401,
                content={"status": "error", "error": {"code": "unauthorized", "message": "Empty bearer token"}},
            )

        settings = get_settings()
        if settings.supabase_enabled:
            try:
                request.state.auth_context = await asyncio.to_thread(
                    verify_access_token_to_auth_context, token, settings
                )
            except SupabaseAuthError:
                return _unauthorized()
            return await call_next(request)

        validator = getattr(request.app.state, "jwt_validator", None)
        if validator is None:
            return _unauthorized("Gateway authentication is not configured (set SUPABASE_* or AUTH_JWT_*)")

        try:
            auth_context = await asyncio.to_thread(validator.verify_to_auth_context, token)
        except JwtVerifyError:
            return _unauthorized()

        request.state.auth_context = auth_context
        return await call_next(request)
