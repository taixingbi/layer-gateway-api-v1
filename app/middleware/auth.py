import asyncio

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.middleware.paths import PUBLIC_PROBE_PATHS
from app.services.jwt_validator import JwtVerifyError


def _split_csv(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def _unauthorized(message: str = "Invalid or expired bearer token") -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"status": "error", "error": {"code": "unauthorized", "message": message}},
    )


def _stub_auth_context(settings) -> dict:
    roles = _split_csv(settings.auth_stub_roles or "")
    if not roles:
        roles = ["customer"]
    return {
        "user_id": settings.auth_stub_user_id,
        "tenant_id": settings.auth_stub_tenant_id,
        "roles": roles,
        "groups": _split_csv(settings.auth_stub_groups or ""),
        "teams": _split_csv(settings.auth_stub_teams or ""),
    }


class AuthMiddleware(BaseHTTPMiddleware):
    """Authenticate incoming requests and attach trusted auth context."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Keep health checks unauthenticated for probes and uptime monitors.
        if request.url.path in PUBLIC_PROBE_PATHS:
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
        if settings.auth_mode == "stub":
            request.state.auth_context = _stub_auth_context(settings)
            return await call_next(request)

        validator = getattr(request.app.state, "jwt_validator", None)
        if validator is None:
            return _unauthorized()

        try:
            auth_context = await asyncio.to_thread(validator.verify_to_auth_context, token)
        except JwtVerifyError:
            return _unauthorized()

        request.state.auth_context = auth_context
        return await call_next(request)
