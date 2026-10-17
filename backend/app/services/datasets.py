"""Dataset use cases: create, add version, delete, and the ingestion job."""

import uuid
from pathlib import Path
from typing import BinaryIO

import structlog
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.db.repository import DatasetRepository, DatasetVersionRepository
from app.db.session import get_sessionmaker
from app.models.dataset import Dataset, DatasetVersion, VersionStatus
from app.models.user import User
from app.services import ingest, storage, upload_validation

logger = structlog.get_logger()


def create_dataset(
    db: Session,
    user: User,
    *,
    name: str,
    description: str,
    filename: str | None,
    file: BinaryIO,
    sheet: str | None,
) -> tuple[Dataset, DatasetVersion]:
    """Create a dataset with version 1 from an upload (caller commits, then enqueues)."""
    ext = upload_validation.file_extension(filename)
    upload_validation.check_head(file, ext)
    repo = DatasetRepository(db, user.tenant_id)
    dataset = repo.add(name=name.strip(), description=description.strip(), created_by=user.id)
    db.flush()
    version = _store_version(db, user, dataset, ext, filename, file, sheet)
    return dataset, version


def add_version(
    db: Session,
    user: User,
    dataset: Dataset,
    *,
    filename: str | None,
    file: BinaryIO,
    sheet: str | None,
) -> DatasetVersion:
    """Add a new version to an existing dataset (caller commits, then enqueues)."""
    ext = upload_validation.file_extension(filename)
    upload_validation.check_head(file, ext)
    # Lock the dataset row so concurrent uploads get distinct version numbers.
    DatasetRepository(db, user.tenant_id).get_or_404(dataset.id, for_update=True)
    return _store_version(db, user, dataset, ext, filename, file, sheet)


def _store_version(
    db: Session,
    user: User,
    dataset: Dataset,
    ext: str,
    filename: str | None,
    file: BinaryIO,
    sheet: str | None,
) -> DatasetVersion:
    """Save the original file and create the version row; undo the file on any error."""
    backend = storage.get_storage()
    versions = DatasetVersionRepository(db, user.tenant_id)
    number = versions.next_version_number(dataset.id)
    prefix = storage.version_prefix(user.tenant_id, dataset.id, number)
    try:
        written = backend.save_stream(
            f"{prefix}/original.{ext}", file, get_settings().max_upload_mb * 1024 * 1024
        )
        if written == 0:
            raise UnprocessableError("The file is empty.", code="empty_file")
        if ext == "xlsx":
            path = backend.local_path(f"{prefix}/original.{ext}")
            upload_validation.check_saved_xlsx(path)
            _check_sheet(path, sheet)
        version = versions.add(
            dataset_id=dataset.id,
            version=number,
            original_filename=upload_validation.sanitize_display_name(filename),
            original_path=f"{prefix}/original.{ext}",
            size_bytes=written,
            status=VersionStatus.uploaded.value,
            sheet=sheet,
        )
        db.flush()
    except BaseException:
        backend.delete_prefix(prefix)
        raise
    if dataset.current_version_id is None:
        dataset.status = VersionStatus.uploaded.value
    return version


def _check_sheet(path: Path, sheet: str | None) -> None:
    try:
        names = ingest.list_sheets(path)
    except ingest.IngestError as exc:
        raise UnprocessableError(str(exc), code="corrupt_file") from exc
    if not names:
        raise UnprocessableError("The workbook has no sheets.", code="empty_file")
    if sheet is not None and sheet not in names:
        raise UnprocessableError(
            f"Sheet '{sheet}' was not found.", code="unknown_sheet", details={"sheets": names}
        )


def enqueue_ingest(db: Session, tenant_id: uuid.UUID, version: DatasetVersion) -> None:
    """Queue the ingestion task; if the queue is unreachable the version is marked failed."""
    from app.workers.tasks import ingest_dataset  # local import: tasks import this module

    try:
        ingest_dataset.delay(str(tenant_id), str(version.id))
        db.expire_all()  # an inline (eager) run updates rows through another session
    except Exception:
        logger.exception("ingest_enqueue_failed", version_id=str(version.id))
        version.status = VersionStatus.failed.value
        version.error = "Processing could not be started. Please try again later."
        dataset = DatasetRepository(db, tenant_id).get_including_deleted(version.dataset_id)
        if dataset is not None and dataset.current_version_id is None:
            dataset.status = VersionStatus.failed.value
        db.commit()


def delete_dataset(db: Session, repo: DatasetRepository, dataset_id: uuid.UUID) -> None:
    """Soft-delete a dataset; its versions can no longer be read or queried."""
    dataset = repo.get_or_404(dataset_id)
    repo.soft_delete(dataset)
    db.commit()


def current_ready_version(db: Session, dataset: Dataset) -> DatasetVersion:
    """Return the dataset's current version or explain why it cannot be read yet."""
    versions = DatasetVersionRepository(db, dataset.tenant_id)
    version = versions.get(dataset.current_version_id) if dataset.current_version_id else None
    if version is None or version.status != VersionStatus.ready.value:
        history = versions.for_dataset(dataset.id)
        latest = history[0] if history else None
        if latest is not None and latest.status == VersionStatus.failed.value:
            raise ConflictError(f"Processing failed: {latest.error}", code="dataset_failed")
        raise ConflictError("The dataset is still being processed.", code="dataset_not_ready")
    return version


def get_excel_version(db: Session, dataset: Dataset) -> DatasetVersion:
    """Return the latest version if it is an Excel file."""
    versions = DatasetVersionRepository(db, dataset.tenant_id).for_dataset(dataset.id)
    if not versions:
        raise NotFoundError("Dataset has no versions")
    latest = versions[0]
    if not latest.original_path.endswith(".xlsx"):
        raise UnprocessableError("Only Excel datasets have sheets.", code="not_excel")
    return latest


def run_ingest(tenant_id: uuid.UUID, version_id: uuid.UUID) -> None:
    """Convert one uploaded version to Parquet and record the outcome (worker entry point)."""
    settings = get_settings()
    backend = storage.get_storage()
    with get_sessionmaker()() as db:
        version = DatasetVersionRepository(db, tenant_id).get(version_id)
        if version is None or version.status != VersionStatus.uploaded.value:
            return
        dataset = DatasetRepository(db, tenant_id).get_including_deleted(version.dataset_id)
        if dataset is None:
            return
        version.status = VersionStatus.processing.value
        if dataset.current_version_id is None:
            dataset.status = VersionStatus.processing.value
        db.commit()

        prefix = storage.version_prefix(tenant_id, dataset.id, version.version)
        parquet_key = f"{prefix}/data.parquet"
        try:
            ext = version.original_path.rsplit(".", 1)[-1]
            result = ingest.ingest_file(
                backend.local_path(version.original_path),
                backend.new_path(parquet_key),
                ext,
                sheet=version.sheet,
                max_rows=settings.max_rows,
            )
        except ingest.IngestError as exc:
            _fail(db, dataset, version, str(exc))
            return
        except Exception:
            logger.exception("ingest_failed", version_id=str(version_id))
            _fail(db, dataset, version, "Unexpected error while processing the file.")
            return

        version.parquet_path = parquet_key
        version.row_count = result.row_count
        version.col_count = result.col_count
        version.column_map = result.column_map
        version.encoding = result.encoding
        version.delimiter = result.delimiter
        version.sheet = result.sheet or version.sheet
        version.warnings = result.warnings or None
        version.error = None
        version.status = VersionStatus.ready.value
        current = (
            DatasetVersionRepository(db, tenant_id).get(dataset.current_version_id)
            if dataset.current_version_id
            else None
        )
        if current is None or current.version < version.version:
            dataset.current_version_id = version.id  # never move back to an older version
        dataset.status = VersionStatus.ready.value
        db.commit()
        logger.info("ingest_ready", version_id=str(version_id), rows=result.row_count)


def _fail(db: Session, dataset: Dataset, version: DatasetVersion, message: str) -> None:
    version.status = VersionStatus.failed.value
    version.error = message
    if dataset.current_version_id is None:
        dataset.status = VersionStatus.failed.value
    db.commit()
