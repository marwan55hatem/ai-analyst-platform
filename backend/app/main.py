"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app import __version__
from app.api.v1 import health
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    settings = get_settings()
    configure_logging(settings.log_level)
    application = FastAPI(title=settings.app_name, version=__version__)
    application.add_middleware(RequestIdMiddleware)
    application.include_router(health.router, prefix="/api/v1")
    return application


app = create_app()
