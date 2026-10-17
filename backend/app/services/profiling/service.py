"""Profile stage use cases: run it in the worker, read it, override roles, rerun it."""

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.db.repository import (
    ColumnProfileRepository,
    DatasetRepository,
    DatasetVersionRepository,
)
from app.db.session import get_sessionmaker
from app.llm import get_llm_client
from app.models.dataset import Dataset, DatasetVersion, ProfileStatus, VersionStatus
from app.models.profile import ROLES, ColumnProfile
from app.services import storage
from app.services.profiling.compute import compute_profile

logger = structlog.get_logger()

PROFILE_FAILED_MESSAGE = "Profiling failed. Please try the rerun button or upload the file again."
PROFILE_START_FAILED = "Profiling could not be started. Please try again later."
PROFILE_STUCK_ERROR = "Profiling timed out. Please rerun it."


# ---- worker side ---------------------------------------------------------------------------


def run_profile(tenant_id: uuid.UUID, version_id: uuid.UUID) -> None:
    """Profile one ready version and store the result (worker entry point)."""
    with get_sessionmaker()() as db:
        version = DatasetVersionRepository(db, tenant_id).get(version_id)
        if (
            version is None
            or version.status != VersionStatus.ready.value
            or version.parquet_path is None
            or version.profile_status != ProfileStatus.pending.value
        ):
            return
        version.profile_status = ProfileStatus.running.value
        version.profile_started_at = datetime.now(UTC)
        version.profile_error = None
        db.commit()
        try:
            _profile_and_store(db, tenant_id, version)
        except Exception:
            logger.exception("profile_failed", version_id=str(version_id))
            db.rollback()
            version = DatasetVersionRepository(db, tenant_id).get(version_id)
            if version is not None:
                version.profile_status = ProfileStatus.failed.value
                version.profile_error = PROFILE_FAILED_MESSAGE
                db.commit()


def _inherited_roles(db: Session, tenant_id: uuid.UUID, version: DatasetVersion) -> dict[str, str]:
    """Roles the user chose earlier: on this version or on the version it was cleaned from."""
    repo = ColumnProfileRepository(db, tenant_id)
    roles: dict[str, str] = {}
    if version.source_version_id is not None:
        roles.update(
            {
                c.name: c.semantic_role
                for c in repo.for_version(version.source_version_id)
                if c.role_source == "user"
            }
        )
    roles.update(
        {c.name: c.semantic_role for c in repo.for_version(version.id) if c.role_source == "user"}
    )
    return roles


def _profile_and_store(db: Session, tenant_id: uuid.UUID, version: DatasetVersion) -> None:
    assert version.parquet_path is not None
    parquet = storage.get_storage().local_path(version.parquet_path)
    result = compute_profile(
        parquet,
        version.column_map,
        user_roles=_inherited_roles(db, tenant_id, version),
        llm=get_llm_client(),
    )
    db.execute(
        delete(ColumnProfile).where(
            ColumnProfile.tenant_id == tenant_id, ColumnProfile.dataset_version_id == version.id
        )
    )
    repo = ColumnProfileRepository(db, tenant_id)
    for column in result.columns:
        repo.add(
            dataset_version_id=version.id,
            name=column.name,
            original_name=column.original_name,
            inferred_type=column.inferred_type,
            semantic_role=column.semantic_role,
            role_confidence=column.role_confidence,
            role_source=column.role_source,
            stats=column.stats,
            pii_flag=column.pii_flag,
        )
    version.dataset_profile = result.dataset
    version.quality = result.quality.as_dict()
    version.quality_score = result.quality.score
    version.cleaning_plan = [fix.as_dict() for fix in result.plan]
    version.profile_status = ProfileStatus.ready.value
    version.profile_error = None
    db.commit()
    logger.info(
        "profile_ready",
        version_id=str(version.id),
        score=result.quality.score,
        columns=len(result.columns),
    )


def enqueue_profile(db: Session, tenant_id: uuid.UUID, version: DatasetVersion) -> None:
    """Queue the profile task; if the queue is unreachable only the profile is marked failed."""
    from app.workers.tasks import profile_dataset  # local import: tasks import this package

    try:
        profile_dataset.delay(str(tenant_id), str(version.id))
        db.expire_all()  # an inline (eager) run updates rows through another session
    except Exception:
        logger.exception("profile_enqueue_failed", version_id=str(version.id))
        version.profile_status = ProfileStatus.failed.value
        version.profile_error = PROFILE_START_FAILED
        db.commit()


# ---- request side --------------------------------------------------------------------------


def resolve_version(db: Session, dataset: Dataset, number: int | None = None) -> DatasetVersion:
    """Return the requested version (by number) or the dataset's current ready version."""
    from app.services.datasets import current_ready_version

    if number is None:
        return current_ready_version(db, dataset)
    for version in DatasetVersionRepository(db, dataset.tenant_id).for_dataset(dataset.id):
        if version.version == number:
            return version
    raise NotFoundError(f"Version {number} not found")


def require_profile(version: DatasetVersion) -> None:
    """Raise a clear 409 unless the version's profile is ready."""
    if version.status != VersionStatus.ready.value:
        raise ConflictError("The dataset is still being processed.", code="dataset_not_ready")
    if version.profile_status == ProfileStatus.failed.value:
        raise ConflictError(f"Profiling failed: {version.profile_error}", code="profile_failed")
    if version.profile_status != ProfileStatus.ready.value:
        raise ConflictError("The profile is still being computed.", code="profile_not_ready")


def column_profiles(db: Session, version: DatasetVersion) -> list[ColumnProfile]:
    """Return a ready version's column profiles."""
    return ColumnProfileRepository(db, version.tenant_id).for_version(version.id)


def override_role(db: Session, version: DatasetVersion, name: str, role: str) -> ColumnProfile:
    """Set a column's role by the user (source `user`, confidence 1.0)."""
    require_profile(version)
    if role not in ROLES:
        raise UnprocessableError(f"Unknown role '{role}'.", code="unknown_role")
    column = ColumnProfileRepository(db, version.tenant_id).get_by_name(version.id, name)
    if column is None:
        raise NotFoundError(f"Column '{name}' not found")
    column.semantic_role = role
    column.role_confidence = 1.0
    column.role_source = "user"
    db.commit()
    return column


def rerun_profile(db: Session, tenant_id: uuid.UUID, version: DatasetVersion) -> DatasetVersion:
    """Reset a ready or failed profile to pending and queue it again."""
    locked = DatasetVersionRepository(db, tenant_id).get_or_404(version.id, for_update=True)
    if locked.status != VersionStatus.ready.value:
        raise ConflictError("The dataset is still being processed.", code="dataset_not_ready")
    if locked.profile_status == ProfileStatus.running.value:
        raise ConflictError("The profile is being computed right now.", code="profile_running")
    locked.profile_status = ProfileStatus.pending.value
    locked.profile_error = None
    locked.profile_started_at = None
    db.commit()
    enqueue_profile(db, tenant_id, locked)
    db.refresh(locked)
    return locked


def dataset_for_version(db: Session, tenant_id: uuid.UUID, version: DatasetVersion) -> Dataset:
    """Return the (not deleted) dataset a version belongs to."""
    dataset = DatasetRepository(db, tenant_id).get(version.dataset_id)
    if dataset is None:
        raise NotFoundError("Dataset not found")
    return dataset


def profile_payload(db: Session, version: DatasetVersion) -> dict[str, Any]:
    """Return the profile response body for a ready version."""
    require_profile(version)
    return {
        "version_id": version.id,
        "version": version.version,
        "profile_status": version.profile_status,
        "row_count": version.row_count,
        "column_count": version.col_count,
        "dataset": version.dataset_profile or {},
        "columns": column_profiles(db, version),
    }
