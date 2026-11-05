"""Report endpoints: queue a PDF report, list and poll reports, download and delete them."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import FileResponse

from app.api import responses
from app.api.deps import CurrentUser, DatasetRepo, DbSession, Limiter
from app.core.config import get_settings
from app.db.repository import ReportRepository
from app.schemas.reports import ReportCreate, ReportOut, ReportPage
from app.services import reports

router = APIRouter(
    tags=["reports"],
    responses={**responses.UNAUTHORIZED, **responses.NOT_FOUND},
)

HOUR = 3600


@router.post(
    "/datasets/{dataset_id}/reports",
    response_model=ReportOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a PDF report of a version's latest finished analysis; poll /reports/{id}",
    responses={**responses.CONFLICT, **responses.RATE_LIMITED, **responses.UNPROCESSABLE},
)
def create_report(
    dataset_id: uuid.UUID,
    body: ReportCreate,
    user: CurrentUser,
    db: DbSession,
    repo: DatasetRepo,
    limiter: Limiter,
) -> ReportOut:
    """Queue a report. 429 report_busy while another one is being generated."""
    limiter.hit(f"reports:{user.id}", get_settings().report_rate_limit_per_hour, HOUR)
    report = reports.start_report(db, user, repo.get_or_404(dataset_id), body.version, body.locale)
    db.refresh(report)
    return ReportOut.model_validate(report)


@router.get("/reports", response_model=ReportPage, summary="The company's reports, newest first")
def list_reports(
    user: CurrentUser,
    db: DbSession,
    dataset_id: Annotated[uuid.UUID | None, Query()] = None,
    status_filter: Annotated[
        Literal["queued", "running", "completed", "failed"] | None, Query(alias="status")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReportPage:
    """Return one page of reports with optional dataset and status filters."""
    rows, total = ReportRepository(db, user.tenant_id).page(
        dataset_id=dataset_id, status=status_filter, limit=limit, offset=offset
    )
    return ReportPage(items=[ReportOut.model_validate(r) for r in rows], total=total)


@router.get("/reports/{report_id}", response_model=ReportOut, summary="One report and its status")
def get_report(report_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ReportOut:
    """Return a report of the caller's company."""
    return ReportOut.model_validate(ReportRepository(db, user.tenant_id).get_or_404(report_id))


@router.get(
    "/reports/{report_id}/download",
    response_class=FileResponse,
    summary="Download the PDF (attachment with a sanitized file name)",
    responses={
        200: {"content": {"application/pdf": {}}, "description": "The report PDF"},
        **responses.CONFLICT,
        **responses.RATE_LIMITED,
    },
)
def download_report(
    report_id: uuid.UUID, user: CurrentUser, db: DbSession, limiter: Limiter
) -> FileResponse:
    """Stream a completed report; the storage path never leaves the server."""
    limiter.hit(f"reports:{user.id}", get_settings().report_rate_limit_per_hour, HOUR)
    path, disposition = reports.download(db, user.tenant_id, report_id)
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/reports/{report_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a report and its file (its creator, an owner or an admin)",
    responses={**responses.FORBIDDEN, **responses.CONFLICT},
)
def delete_report(report_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    """Delete a finished report."""
    reports.delete_report(db, user, report_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
