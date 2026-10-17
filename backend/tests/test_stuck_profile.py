"""Profiles stuck in `running` are failed by the same sweeper, without touching the file."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.dataset import DatasetVersion
from app.services.datasets import fail_stuck_ingestions
from app.services.profiling.service import PROFILE_STUCK_ERROR
from tests.dataset_helpers import csv_bytes, upload_ready
from tests.helpers import API, Account, register

FROZEN_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def _version_with_profile(
    client: TestClient, db: Session, owner: Account, status: str, started: datetime | None
) -> DatasetVersion:
    made = upload_ready(client, owner, csv_bytes([[1, "a"], [2, "b"]], ["n", "label"]), "p.csv")
    version = db.get(DatasetVersion, uuid.UUID(made["versions"][0]["id"]))
    assert version is not None
    version.profile_status = status
    version.profile_started_at = started
    db.commit()
    return version


def test_profile_running_longer_than_the_limit_is_failed(client: TestClient, db: Session) -> None:
    owner = register(client)
    version = _version_with_profile(
        client, db, owner, "running", FROZEN_NOW - timedelta(minutes=31)
    )

    assert fail_stuck_ingestions(db, now=FROZEN_NOW) == 1

    db.refresh(version)
    assert version.profile_status == "failed" and version.profile_error == PROFILE_STUCK_ERROR
    assert version.status == "ready"  # the dataset itself stays usable
    shown = client.get(f"{API}/datasets/{version.dataset_id}", headers=owner.headers).json()
    assert shown["status"] == "ready"
    assert shown["versions"][0]["profile_status"] == "failed"
    preview = client.get(f"{API}/datasets/{version.dataset_id}/preview", headers=owner.headers)
    assert preview.status_code == 200


def test_recent_and_finished_profiles_are_left_alone(client: TestClient, db: Session) -> None:
    owner = register(client)
    recent = _version_with_profile(client, db, owner, "running", FROZEN_NOW - timedelta(minutes=5))
    done = _version_with_profile(client, db, owner, "ready", FROZEN_NOW - timedelta(hours=5))
    waiting = _version_with_profile(client, db, owner, "pending", None)

    assert fail_stuck_ingestions(db, now=FROZEN_NOW) == 0

    for version, expected in ((recent, "running"), (done, "ready"), (waiting, "pending")):
        db.refresh(version)
        assert version.profile_status == expected


def test_a_running_profile_without_a_start_time_falls_back_to_created_at(
    client: TestClient, db: Session
) -> None:
    owner = register(client)
    version = _version_with_profile(client, db, owner, "running", None)
    far_future = datetime.now(UTC) + timedelta(hours=2)
    assert fail_stuck_ingestions(db, now=far_future) == 1
    db.refresh(version)
    assert version.profile_status == "failed"


def test_the_sweeper_counts_stuck_versions_and_profiles_together(
    client: TestClient, db: Session
) -> None:
    owner = register(client)
    _version_with_profile(client, db, owner, "running", FROZEN_NOW - timedelta(minutes=90))
    other = _version_with_profile(client, db, owner, "ready", None)
    other.status = "processing"
    other.processing_started_at = FROZEN_NOW - timedelta(minutes=90)
    db.commit()
    assert fail_stuck_ingestions(db, now=FROZEN_NOW) == 2
