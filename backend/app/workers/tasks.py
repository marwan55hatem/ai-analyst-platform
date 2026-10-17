"""Celery tasks."""

import uuid

from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.ping")
def ping() -> str:
    """Return "pong"; used to verify the worker pipeline."""
    return "pong"


@celery_app.task(name="app.workers.tasks.ingest_dataset")
def ingest_dataset(tenant_id: str, version_id: str) -> None:
    """Convert an uploaded dataset version to Parquet."""
    from app.services.datasets import run_ingest

    run_ingest(uuid.UUID(tenant_id), uuid.UUID(version_id))


@celery_app.task(name="app.workers.tasks.fail_stuck_ingestions")
def fail_stuck_ingestions() -> int:
    """Fail dataset versions stuck in `processing` (runs from Celery beat)."""
    from app.db.session import get_sessionmaker
    from app.services.datasets import fail_stuck_ingestions as sweep

    with get_sessionmaker()() as db:
        return sweep(db)
