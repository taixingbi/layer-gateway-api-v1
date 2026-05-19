"""FastAPI application factory, lifespan, and middleware stack."""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging, log_event
from app.middleware.access_log import StructuredAccessLogMiddleware
from app.middleware.auth import AuthMiddleware
from app.middleware.inflight import InflightLimitMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.routes.auth import router as auth_router
from app.routes.chat import router as chat_router
from app.routes.feedback import router as feedback_router
from app.routes.health import router as health_router
from app.routes.metrics import router as metrics_router
from app.routes.profile import router as profile_router
from app.services.jwt_validator import JwtValidator
from app.services.orchestrator_client import OrchestratorClient
from app.services.supabase_client import admin_client_configured, service_key_role


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Wire auth validator and orchestrator HTTP client for the app lifetime."""
    settings = get_settings()
    role = service_key_role() if settings.supabase_enabled else None
    log_event(
        "startup_auth",
        supabase_enabled=settings.supabase_enabled,
        jwt_fallback=not settings.supabase_enabled,
        supabase_admin_client=admin_client_configured(),
        supabase_service_key_role=role,
        note="supabase=get_user+profiles; else JWKS_verify",
    )
    if settings.supabase_enabled and role == "anon":
        log_event(
            "startup_auth_warn",
            message=(
                "SUPABASE_SERVICE_KEY looks like the anon key; username login may fail. "
                "Use the service_role secret or run sql/username_login.sql."
            ),
        )
    if settings.supabase_enabled:
        app.state.jwt_validator = None
    else:
        app.state.jwt_validator = JwtValidator(settings)
    timeout = httpx.Timeout(settings.orchestrator_timeout_ms / 1000)
    async with httpx.AsyncClient(base_url=settings.orchestrator_base_url, timeout=timeout) as client:
        app.state.orchestrator_client = OrchestratorClient(client=client, settings=settings)
        yield


def create_app() -> FastAPI:
    """Build the gateway FastAPI app with middleware and route routers."""
    settings = get_settings()
    configure_logging(settings.env)

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    # Order: last added = outermost on request. Target: request_id → access log → auth → inflight → routes.
    app.add_middleware(InflightLimitMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(StructuredAccessLogMiddleware)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(auth_router)
    app.include_router(profile_router)
    app.include_router(chat_router)
    app.include_router(feedback_router)
    app.include_router(health_router)
    app.include_router(metrics_router)
    return app


app = create_app()
