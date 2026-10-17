"""Celery tasks."""

from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.ping")
def ping() -> str:
    """Return "pong"; used to verify the worker pipeline."""
    return "pong"
