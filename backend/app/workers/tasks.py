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


@celery_app.task(name="app.workers.tasks.profile_dataset")
def profile_dataset(tenant_id: str, version_id: str) -> None:
    """Profile a ready dataset version (runs automatically after ingestion or cleaning)."""
    from app.services.profiling.service import run_profile

    run_profile(uuid.UUID(tenant_id), uuid.UUID(version_id))


@celery_app.task(name="app.workers.tasks.apply_cleaning")
def apply_cleaning(tenant_id: str, version_id: str, selection: dict[str, str | None]) -> None:
    """Build the cleaned Parquet of a new version from the chosen fixes."""
    from app.services.cleaning import run_cleaning

    run_cleaning(uuid.UUID(tenant_id), uuid.UUID(version_id), selection)
