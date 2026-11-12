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
        "fail-stuck-reports": {
            "task": "app.workers.tasks.fail_stuck_reports",
            "schedule": 600.0,
        },
        "purge-expired-reports": {
            "task": "app.workers.tasks.purge_expired_reports",
            "schedule": 86_400.0,  # daily
        },
        "check-data-freshness": {
            "task": "app.workers.tasks.check_data_freshness",
            "schedule": 86_400.0,  # daily; only companies that turned the check on
        },
    },
)


@signals.worker_init.connect
@signals.beat_init.connect
def startup_checks(**_: Any) -> None:
    """Refuse unsafe production settings and warn about a derived pseudonym key (worker, beat)."""
    settings = get_settings()
    settings.ensure_production_safe()
    warn_derived_pseudonym_secret(settings)
