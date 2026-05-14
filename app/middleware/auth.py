from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.middleware.paths import PUBLIC_PROBE_PATHS


def _split_csv(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


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

        # Current MVP uses stub auth claims from settings; replace with IdP lookup.
        settings = get_settings()
        roles = _split_csv(settings.auth_stub_roles or "")
        if not roles:
            roles = ["customer"]
        request.state.auth_context = {
            "user_id": settings.auth_stub_user_id,
            "tenant_id": settings.auth_stub_tenant_id,
            "roles": roles,
            "groups": _split_csv(settings.auth_stub_groups or ""),
            "teams": _split_csv(settings.auth_stub_teams or ""),
        }

        # Hand off to next layer after attaching trusted user context.
        return await call_next(request)
