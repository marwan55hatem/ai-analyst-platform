"""Dataset endpoints: upload, versions, list, detail, delete, sheets, preview, profile, cleaning."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status

from app.api import responses
from app.api.deps import CurrentUser, DatasetRepo, DbSession, require_role
from app.core.errors import ErrorResponse, UnprocessableError
from app.db.repository import DatasetVersionRepository
from app.models.dataset import Dataset
from app.models.user import Role
from app.schemas.dataset import (
    DatasetDetail,
    DatasetOut,
    DatasetVersionOut,
    PreviewColumn,
    PreviewOut,
    SheetsOut,
)
from app.schemas.profile import (
    ApplyRequest,
    CleaningPlanOut,
    ColumnProfileOut,
    FixOut,
    ProfileOut,
    QualityOut,
    RoleUpdate,
)
from app.services import cleaning, ingest
from app.services import datasets as service
from app.services.profiling import service as profiling
from app.services.query import SafeQueryService
from app.services.storage import get_storage

router = APIRouter(
    prefix="/datasets",
    tags=["datasets"],
    responses={**responses.UNAUTHORIZED},
)

UPLOAD_ERRORS: dict[int | str, dict[str, object]] = {
    413: {"model": ErrorResponse},
    415: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
Name = Annotated[str, Form(min_length=1, max_length=200, pattern=r"\S")]
Description = Annotated[str, Form(max_length=2000)]
Sheet = Annotated[str | None, Form(max_length=255)]


def _summary(db: DbSession, dataset: Dataset) -> DatasetOut:
    versions = DatasetVersionRepository(db, dataset.tenant_id).for_dataset(dataset.id)
    out = DatasetOut.model_validate(dataset)
    out.latest_version = DatasetVersionOut.model_validate(versions[0]) if versions else None
    return out


def _detail(db: DbSession, dataset: Dataset) -> DatasetDetail:
    versions = DatasetVersionRepository(db, dataset.tenant_id).for_dataset(dataset.id)
    out = DatasetDetail.model_validate(dataset)
    out.versions = [DatasetVersionOut.model_validate(v) for v in versions]
    out.latest_version = out.versions[0] if out.versions else None
    return out


@router.post(
    "",
    response_model=DatasetDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file and create a dataset (version 1)",
    responses=UPLOAD_ERRORS,
)
def create_dataset(
    user: CurrentUser,
    db: DbSession,
    file: Annotated[UploadFile, File()],
    name: Name,
    description: Description = "",
    sheet: Sheet = None,
) -> DatasetDetail:
    """Save the file, then process it in the background; poll GET /datasets/{id}."""
    dataset, version = service.create_dataset(
        db,
        user,
        name=name,
        description=description,
        filename=file.filename,
        file=file.file,
        sheet=sheet,
    )
    db.commit()
    service.enqueue_ingest(db, user.tenant_id, version)
    db.refresh(dataset)
    return _detail(db, dataset)


@router.post(
    "/{dataset_id}/versions",
    response_model=DatasetDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new version of a dataset",
    responses={**UPLOAD_ERRORS, **responses.NOT_FOUND},
)
def create_version(
    dataset_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    repo: DatasetRepo,
    file: Annotated[UploadFile, File()],
    sheet: Sheet = None,
) -> DatasetDetail:
    """Add a version; the current version stays active until the new one is ready."""
    dataset = repo.get_or_404(dataset_id)
    version = service.add_version(
        db, user, dataset, filename=file.filename, file=file.file, sheet=sheet
    )
    db.commit()
    service.enqueue_ingest(db, user.tenant_id, version)
    db.refresh(dataset)
    return _detail(db, dataset)


@router.get("", response_model=list[DatasetOut], summary="List datasets")
def list_datasets(
    db: DbSession,
    repo: DatasetRepo,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DatasetOut]:
    """Return the tenant's datasets, newest first."""
    return [_summary(db, d) for d in repo.list_recent(limit=limit, offset=offset)]


@router.get(
    "/{dataset_id}",
    response_model=DatasetDetail,
    summary="Get a dataset and its versions (poll this for processing status)",
    responses=responses.NOT_FOUND,
)
def get_dataset(dataset_id: uuid.UUID, db: DbSession, repo: DatasetRepo) -> DatasetDetail:
    """Return one dataset with its versions."""
    return _detail(db, repo.get_or_404(dataset_id))


@router.delete(
    "/{dataset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a dataset (owner or admin)",
    dependencies=[Depends(require_role(Role.owner, Role.admin))],
    responses={**responses.NOT_FOUND, **responses.FORBIDDEN},
)
def delete_dataset(dataset_id: uuid.UUID, db: DbSession, repo: DatasetRepo) -> None:
    """Hide the dataset; it can no longer be read or queried."""
    service.delete_dataset(db, repo, dataset_id)


@router.get(
    "/{dataset_id}/sheets",
    response_model=SheetsOut,
    summary="List the sheets of an Excel dataset",
    responses={**responses.NOT_FOUND, **responses.UNPROCESSABLE},
)
def list_sheets(dataset_id: uuid.UUID, db: DbSession, repo: DatasetRepo) -> SheetsOut:
    """Return sheet names read from the original workbook."""
    version = service.get_excel_version(db, repo.get_or_404(dataset_id))
    try:
        names = ingest.list_sheets(get_storage().local_path(version.original_path))
    except ingest.IngestError as exc:
        raise UnprocessableError(str(exc), code="corrupt_file") from exc
    return SheetsOut(sheets=names)


@router.get(
    "/{dataset_id}/preview",
    response_model=PreviewOut,
    summary="First rows and the column list of the current version",
    responses={**responses.NOT_FOUND, **responses.CONFLICT},
)
def preview_dataset(
    dataset_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    repo: DatasetRepo,
    rows: Annotated[int, Query(ge=1, le=500)] = 50,
) -> PreviewOut:
    """Run a bounded SELECT through the safe query layer."""
    version = service.current_ready_version(db, repo.get_or_404(dataset_id))
    result = SafeQueryService(db).run(user.tenant_id, version.id, "SELECT * FROM data", rows)
    originals = {m["normalized"]: m["original"] for m in (version.column_map or [])}
    return PreviewOut(
        version_id=version.id,
        columns=[
            PreviewColumn(name=n, original=originals.get(n), type=t)
            for n, t in zip(result.columns, result.column_types, strict=True)
        ],
        rows=result.rows,
        row_count=len(result.rows),
        total_rows=version.row_count,
    )


VersionNumber = Annotated[
    int | None, Query(ge=1, description="Version number; defaults to the current version")
]
PROFILE_ERRORS: dict[int | str, dict[str, object]] = {**responses.NOT_FOUND, **responses.CONFLICT}


@router.get(
    "/{dataset_id}/profile",
    response_model=ProfileOut,
    summary="Column profiles, roles and dataset-level findings of a version",
    responses=PROFILE_ERRORS,
)
def get_profile(
    dataset_id: uuid.UUID, db: DbSession, repo: DatasetRepo, version: VersionNumber = None
) -> ProfileOut:
    """Return the profile; 409 while it is still being computed or if it failed."""
    target = profiling.resolve_version(db, repo.get_or_404(dataset_id), version)
    return ProfileOut.model_validate(profiling.profile_payload(db, target))


@router.get(
    "/{dataset_id}/quality",
    response_model=QualityOut,
    summary="Quality score (0-100) with breakdown and the top 5 issues",
    responses=PROFILE_ERRORS,
)
def get_quality(
    dataset_id: uuid.UUID, db: DbSession, repo: DatasetRepo, version: VersionNumber = None
) -> QualityOut:
    """Return the stored quality report; use ?version=N to compare versions."""
    target = profiling.resolve_version(db, repo.get_or_404(dataset_id), version)
    profiling.require_profile(target)
    return QualityOut(version_id=target.id, version=target.version, **(target.quality or {}))


@router.get(
    "/{dataset_id}/cleaning-plan",
    response_model=CleaningPlanOut,
    summary="Proposed cleaning fixes (never applied automatically)",
    responses=PROFILE_ERRORS,
)
def get_cleaning_plan(
    dataset_id: uuid.UUID, db: DbSession, repo: DatasetRepo, version: VersionNumber = None
) -> CleaningPlanOut:
    """List fixes with risk, affected counts and which ones are recommended (low risk only)."""
    target = profiling.resolve_version(db, repo.get_or_404(dataset_id), version)
    profiling.require_profile(target)
    return CleaningPlanOut(
        version_id=target.id,
        version=target.version,
        quality_score=target.quality_score,
        fixes=[FixOut.model_validate(f) for f in target.cleaning_plan or []],
    )


@router.post(
    "/{dataset_id}/cleaning-plan/apply",
    response_model=DatasetDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Apply the listed fixes by creating a new dataset version",
    responses={**PROFILE_ERRORS, **responses.UNPROCESSABLE},
)
def apply_cleaning_plan(
    dataset_id: uuid.UUID,
    body: ApplyRequest,
    user: CurrentUser,
    db: DbSession,
    repo: DatasetRepo,
    version: VersionNumber = None,
) -> DatasetDetail:
    """Create a new version from the listed fix ids; the source version is never changed.

    Only ids listed here are applied. Null fixes need a strategy other than `keep` to change data.
    """
    dataset = repo.get_or_404(dataset_id)
    source = profiling.resolve_version(db, dataset, version)
    new_version, selection = cleaning.start_cleaning(
        db, user, dataset, source, [(f.id, f.strategy) for f in body.fixes]
    )
    db.commit()
    cleaning.enqueue_cleaning(db, user.tenant_id, new_version, selection)
    db.refresh(dataset)
    return _detail(db, dataset)


@router.post(
    "/{dataset_id}/profile/rerun",
    response_model=DatasetVersionOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Compute the profile again (409 while it is running)",
    responses=PROFILE_ERRORS,
)
def rerun_profile(
    dataset_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    repo: DatasetRepo,
    version: VersionNumber = None,
) -> DatasetVersionOut:
    """Reset a ready or failed profile to pending and queue it again."""
    dataset = repo.get_or_404(dataset_id)
    target = profiling.resolve_version(db, dataset, version)
    return DatasetVersionOut.model_validate(profiling.rerun_profile(db, user.tenant_id, target))


@router.put(
    "/{dataset_id}/columns/{name}/role",
    response_model=ColumnProfileOut,
    summary="Override a column's business role (the source becomes `user`)",
    responses={**PROFILE_ERRORS, **responses.UNPROCESSABLE},
)
def set_column_role(
    dataset_id: uuid.UUID,
    name: str,
    body: RoleUpdate,
    db: DbSession,
    repo: DatasetRepo,
    version: VersionNumber = None,
) -> ColumnProfileOut:
    """Store the user's choice; it survives profile reruns and cleaned versions."""
    target = profiling.resolve_version(db, repo.get_or_404(dataset_id), version)
    return ColumnProfileOut.model_validate(profiling.override_role(db, target, name, body.role))
