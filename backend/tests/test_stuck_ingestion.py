"""Versions stuck in `processing` are failed by the periodic sweeper."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.dataset import Dataset, DatasetVersion, VersionStatus
from app.services import datasets as dataset_service
from app.services.datasets import STUCK_ERROR, fail_stuck_ingestions
from app.workers import tasks
from app.workers.celery_app import celery_app
from tests.dataset_helpers import csv_bytes, upload, upload_ready
from tests.helpers import API, Account, register

FROZEN_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def _processing_version(
    client: TestClient,
    db: Session,
    owner: Account,
    started: datetime | None,
    *,
    first_upload: bool = True,
) -> tuple[Dataset, DatasetVersion]:
    """Create a dataset whose newest version is `processing` since `started`."""
    made = upload_ready(client, owner, csv_bytes([[1]], ["a"]), "p.csv")
    dataset = db.get(Dataset, uuid.UUID(made["id"]))
    version = db.get(DatasetVersion, uuid.UUID(made["versions"][0]["id"]))
    assert dataset is not None and version is not None
    version.status = VersionStatus.processing.value
    version.processing_started_at = started
    if first_upload:  # nothing usable yet, as for a brand-new dataset
        dataset.current_version_id = None
        dataset.status = VersionStatus.processing.value
    db.commit()
    return dataset, version


def test_version_processing_longer_than_the_limit_is_failed(
    client: TestClient, db: Session
) -> None:
    owner = register(client)
    dataset, version = _processing_version(client, db, owner, FROZEN_NOW - timedelta(minutes=31))

    assert fail_stuck_ingestions(db, now=FROZEN_NOW) == 1

    db.refresh(version)
    db.refresh(dataset)
    assert version.status == "failed" and version.error == STUCK_ERROR
    assert version.error == "Processing timed out. Please upload again."
    assert dataset.status == "failed"
    shown = client.get(f"{API}/datasets/{dataset.id}", headers=owner.headers).json()
    assert shown["status"] == "failed"
    assert shown["versions"][0]["error"] == "Processing timed out. Please upload again."


def test_recent_and_other_states_are_left_alone(client: TestClient, db: Session) -> None:
    owner = register(client)
    _, fresh = _processing_version(client, db, owner, FROZEN_NOW - timedelta(minutes=29))
    _, boundary = _processing_version(client, db, owner, FROZEN_NOW - timedelta(minutes=30))
    ready = upload_ready(client, owner, csv_bytes([[1]], ["a"]), "r.csv")
    queued = db.get(DatasetVersion, uuid.UUID(ready["versions"][0]["id"]))
    assert queued is not None
    queued.status = VersionStatus.uploaded.value  # waiting in the queue is not "processing"
    queued.created_at = FROZEN_NOW - timedelta(days=2)
    db.commit()

    assert fail_stuck_ingestions(db, now=FROZEN_NOW) == 0

    db.refresh(fresh)
    db.refresh(boundary)
    db.refresh(queued)
    assert fresh.status == boundary.status == "processing"
    assert queued.status == "uploaded"


def test_clock_decides_a_29_minute_old_job_becomes_stuck_later(
    client: TestClient, db: Session
) -> None:
    owner = register(client)
    _, version = _processing_version(client, db, owner, FROZEN_NOW - timedelta(minutes=29))
    assert fail_stuck_ingestions(db, now=FROZEN_NOW) == 0
    assert fail_stuck_ingestions(db, now=FROZEN_NOW + timedelta(minutes=2)) == 1
    db.refresh(version)
    assert version.status == "failed"
    assert fail_stuck_ingestions(db, now=FROZEN_NOW + timedelta(hours=5)) == 0  # idempotent


def test_failed_new_version_keeps_the_previous_one_usable(client: TestClient, db: Session) -> None:
    owner = register(client)
    dataset, old = _processing_version(
        client, db, owner, FROZEN_NOW - timedelta(minutes=90), first_upload=False
    )
    old.status = VersionStatus.ready.value  # v1 is fine ...
    second = upload(
        client, owner, csv_bytes([[2]], ["a"]), "v2.csv", path=f"/datasets/{dataset.id}/versions"
    ).json()
    newer = db.get(DatasetVersion, uuid.UUID(second["versions"][0]["id"]))
    assert newer is not None
    newer.status = VersionStatus.processing.value  # ... v2 hangs
    newer.processing_started_at = FROZEN_NOW - timedelta(minutes=45)
    db.commit()

    assert fail_stuck_ingestions(db, now=FROZEN_NOW) == 1
    db.refresh(dataset)
    db.refresh(newer)
    assert newer.status == "failed"
    assert dataset.status == "ready"  # the dataset is still served from the old version


def test_rows_without_start_time_fall_back_to_created_at(client: TestClient, db: Session) -> None:
    owner = register(client)
    _, version = _processing_version(client, db, owner, None)
    version.created_at = FROZEN_NOW - timedelta(hours=2)
    db.commit()
    assert fail_stuck_ingestions(db, now=FROZEN_NOW) == 1


def test_timeout_comes_from_the_setting(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(client)
    _, version = _processing_version(client, db, owner, FROZEN_NOW - timedelta(minutes=10))
    assert fail_stuck_ingestions(db, now=FROZEN_NOW) == 0  # default is 30 minutes
    monkeypatch.setattr(get_settings(), "ingest_stuck_minutes", 5)
    assert fail_stuck_ingestions(db, now=FROZEN_NOW) == 1
    assert get_settings().model_fields["ingest_stuck_minutes"].default == 30


def test_celery_task_runs_the_sweep_and_is_scheduled_every_10_minutes(
    client: TestClient, db: Session
) -> None:
    owner = register(client)
    _, version = _processing_version(client, db, owner, datetime.now(UTC) - timedelta(minutes=45))
    assert tasks.fail_stuck_ingestions.apply().get() == 1
    db.refresh(version)
    assert version.status == "failed" and version.error == STUCK_ERROR

    entry = celery_app.conf.beat_schedule["fail-stuck-ingestions"]
    assert entry["task"] == "app.workers.tasks.fail_stuck_ingestions"
    assert entry["schedule"] == 600.0


def test_ingestion_records_when_processing_started(client: TestClient, db: Session) -> None:
    owner = register(client)
    before = datetime.now(UTC)
    made = upload_ready(client, owner, csv_bytes([[1]], ["a"]), "t.csv")
    version = db.get(DatasetVersion, uuid.UUID(made["versions"][0]["id"]))
    assert version is not None and version.processing_started_at is not None
    assert version.processing_started_at >= before - timedelta(seconds=1)
    assert dataset_service.STUCK_ERROR == STUCK_ERROR
