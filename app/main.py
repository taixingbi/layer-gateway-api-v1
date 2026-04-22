from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.middleware.auth import AuthMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.routes.chat import router as chat_router
from app.routes.health import router as health_router
from app.services.orchestrator_client import OrchestratorClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    timeout = httpx.Timeout(settings.orchestrator_timeout_ms / 1000)
    async with httpx.AsyncClient(base_url=settings.orchestrator_base_url, timeout=timeout) as client:
        app.state.orchestrator_client = OrchestratorClient(client=client, settings=settings)
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.env)

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(AuthMiddleware)

    app.include_router(chat_router)
    app.include_router(health_router)
    return app


app = create_app()
