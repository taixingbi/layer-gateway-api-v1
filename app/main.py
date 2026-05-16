from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging, log_event
from app.middleware.access_log import StructuredAccessLogMiddleware
from app.middleware.auth import AuthMiddleware
from app.middleware.inflight import InflightLimitMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.routes.chat import router as chat_router
from app.routes.feedback import router as feedback_router
from app.routes.health import router as health_router
from app.routes.metrics import router as metrics_router
from app.services.jwt_validator import JwtValidator
from app.services.orchestrator_client import OrchestratorClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log_event(
        "startup_auth",
        auth_mode=settings.auth_mode,
        note="stub=dev_bearer_accepted; jwt=JWKS_verify",
    )
    if settings.auth_mode == "jwt":
        app.state.jwt_validator = JwtValidator(settings)
    else:
        app.state.jwt_validator = None
    timeout = httpx.Timeout(settings.orchestrator_timeout_ms / 1000)
    async with httpx.AsyncClient(base_url=settings.orchestrator_base_url, timeout=timeout) as client:
        app.state.orchestrator_client = OrchestratorClient(client=client, settings=settings)
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.env)

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    # Order: last added = outermost on request. Target: request_id → access log → auth → inflight → routes.
    app.add_middleware(InflightLimitMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(StructuredAccessLogMiddleware)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(chat_router)
    app.include_router(feedback_router)
    app.include_router(health_router)
    app.include_router(metrics_router)
    return app


app = create_app()
