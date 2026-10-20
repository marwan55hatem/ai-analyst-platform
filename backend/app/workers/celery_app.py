"""Celery application and beat schedule."""

from typing import Any

from celery import Celery, signals

from app.core.config import get_settings, warn_derived_pseudonym_secret

settings = get_settings()

celery_app = Celery(
    "analyst",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    beat_schedule={
        "fail-stuck-ingestions": {
            "task": "app.workers.tasks.fail_stuck_ingestions",
            "schedule": 600.0,  # every 10 minutes
        },
    },
)


@signals.worker_init.connect
def startup_checks(**_: Any) -> None:
    """Warn once per worker about settings that are unsafe in production."""
    warn_derived_pseudonym_secret(get_settings())
