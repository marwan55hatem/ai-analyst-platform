"""Apply a cleaning plan: validate the choice, create a NEW version, build its Parquet.

The source version (row, original file and Parquet) is never modified. The new version walks
the same status machine as an upload (uploaded > processing > ready), is covered by the same
stuck-job sweeper, obeys the "late version must not overwrite the current one" rule, and is
profiled like any other version once ready.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableError
from app.db.repository import (
    CleaningLogRepository,
    DatasetRepository,
    DatasetVersionRepository,
)
from app.db.session import get_sessionmaker
from app.models.dataset import Dataset, DatasetVersion, ProfileStatus, VersionStatus
from app.models.user import User
from app.services import storage
from app.services.datasets import promote_if_newer
from app.services.profiling.cleaning_apply import apply_fixes
from app.services.profiling.cleaning_plan import KEEP
from app.services.profiling.service import enqueue_profile, require_profile

logger = structlog.get_logger()

CLEANING_FAILED = "Cleaning failed. Your original data is unchanged."


def validate_selection(
    plan: list[dict[str, Any]], choices: list[tuple[str, str | None]]
) -> dict[str, str | None]:
    """Check the requested fix ids and strategies against the stored plan.

    Returns {fix id: strategy}. Only ids listed by the user are ever applied.
    """
    if not choices:
        raise UnprocessableError("Choose at least one fix to apply.", code="no_fixes_selected")
    by_id = {f["id"]: f for f in plan}
    selection: dict[str, str | None] = {}
    for fix_id, strategy in choices:
        fix = by_id.get(fix_id)
        if fix is None:
            raise UnprocessableError(
                f"Unknown fix '{fix_id}'.", code="unknown_fix", details={"fix": fix_id}
            )
        if fix_id in selection:
            raise UnprocessableError(
                f"Fix '{fix_id}' was listed twice.", code="duplicate_fix", details={"fix": fix_id}
            )
        allowed = fix.get("strategies")
        if strategy is not None and (not allowed or strategy not in allowed):
            raise UnprocessableError(
                f"Strategy '{strategy}' is not available for '{fix_id}'.",
                code="invalid_strategy",
                details={"fix": fix_id, "allowed": allowed or []},
            )
        selection[fix_id] = strategy
    changes = [
        fid
        for fid, strategy in selection.items()
        if by_id[fid]["type"] != "nulls" or (strategy or KEEP) != KEEP
    ]
    if not changes:
        raise UnprocessableError(
            "The chosen fixes would not change anything (null handling is set to keep).",
            code="nothing_to_apply",
        )
    return selection


def start_cleaning(
    db: Session,
    user: User,
    dataset: Dataset,
    source: DatasetVersion,
    choices: list[tuple[str, str | None]],
) -> tuple[DatasetVersion, dict[str, str | None]]:
    """Validate the choice and create the new (not yet processed) version."""
    require_profile(source)
    selection = validate_selection(source.cleaning_plan or [], choices)
    DatasetRepository(db, user.tenant_id).get_or_404(dataset.id, for_update=True)
    versions = DatasetVersionRepository(db, user.tenant_id)
    number = versions.next_version_number(dataset.id)
    version = versions.add(
        dataset_id=dataset.id,
        version=number,
        original_filename=source.original_filename,
        original_path=source.original_path,
        size_bytes=source.size_bytes,
        status=VersionStatus.uploaded.value,
        sheet=source.sheet,
        encoding=source.encoding,
        delimiter=source.delimiter,
        source_version_id=source.id,
    )
    db.flush()
    return version, selection


def enqueue_cleaning(
    db: Session, tenant_id: uuid.UUID, version: DatasetVersion, selection: dict[str, str | None]
) -> None:
    """Queue the cleaning task; if the queue is unreachable the new version is marked failed."""
    from app.workers.tasks import apply_cleaning  # local import: tasks import this module

    try:
        apply_cleaning.delay(str(tenant_id), str(version.id), selection)
        db.expire_all()
    except Exception:
        logger.exception("cleaning_enqueue_failed", version_id=str(version.id))
        version.status = VersionStatus.failed.value
        version.error = "Cleaning could not be started. Please try again later."
        db.commit()


def run_cleaning(
    tenant_id: uuid.UUID, version_id: uuid.UUID, selection: dict[str, str | None]
) -> None:
    """Build the cleaned Parquet for a new version (worker entry point)."""
    backend = storage.get_storage()
    with get_sessionmaker()() as db:
        versions = DatasetVersionRepository(db, tenant_id)
        version = versions.get(version_id)
        if version is None or version.status != VersionStatus.uploaded.value:
            return
        source = versions.get(version.source_version_id) if version.source_version_id else None
        dataset = DatasetRepository(db, tenant_id).get_including_deleted(version.dataset_id)
        if source is None or dataset is None or source.parquet_path is None:
            _fail(db, version, CLEANING_FAILED)
            return
        version.status = VersionStatus.processing.value
        version.processing_started_at = datetime.now(UTC)
        db.commit()

        prefix = storage.version_prefix(tenant_id, dataset.id, version.version)
        parquet_key = f"{prefix}/data.parquet"
        try:
            result = apply_fixes(
                backend.local_path(source.parquet_path),
                backend.new_path(parquet_key),
                source.cleaning_plan or [],
                selection,
            )
        except Exception:
            logger.exception("cleaning_failed", version_id=str(version_id))
            _fail(db, version, CLEANING_FAILED)
            return

        version.parquet_path = parquet_key
        version.row_count = result.row_count
        version.col_count = len(result.columns)
        version.column_map = [
            *(source.column_map or []),
            *({"original": c, "normalized": c} for c in result.new_columns),
        ]
        version.warnings = None
        version.error = None
        version.status = VersionStatus.ready.value
        version.profile_status = ProfileStatus.pending.value
        logs = CleaningLogRepository(db, tenant_id)
        for applied in result.applied:
            logs.add(
                version_id=version.id,
                source_version_id=source.id,
                fix_id=applied.fix_id,
                description=applied.description,
                rows_affected=applied.rows_affected,
            )
        promote_if_newer(db, dataset, version)
        db.commit()
        logger.info("cleaning_ready", version_id=str(version_id), rows=result.row_count)
        enqueue_profile(db, tenant_id, version)


def _fail(db: Session, version: DatasetVersion, message: str) -> None:
    version.status = VersionStatus.failed.value
    version.error = message
    db.commit()
