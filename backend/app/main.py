"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1 import analysis, auth, datasets, forecast, health, team
from app.core.config import get_settings, warn_derived_pseudonym_secret
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import REQUEST_ID_HEADER, RequestIdMiddleware


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    settings = get_settings()
    settings.ensure_production_safe()
    configure_logging(settings.log_level)
    warn_derived_pseudonym_secret(settings)
    application = FastAPI(title=settings.app_name, version=__version__)
    register_error_handlers(application)
    application.add_middleware(RequestIdMiddleware)
    origins = settings.cors_origin_list
    if "*" in origins:
        raise ValueError("CORS_ORIGINS must list explicit origins, not '*'")
    if origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
            expose_headers=[REQUEST_ID_HEADER],
        )
    for router in (
        health.router,
        auth.router,
        team.router,
        datasets.router,
        analysis.router,
        forecast.router,
    ):
        application.include_router(router, prefix="/api/v1")
    return application


app = create_app()
