"""Report use cases: queue one report at a time per company, build it in the worker, keep it.

The PDF is built only from the stored results of the newest finished analysis run of the chosen
version (no model call, no live estimate). Files live under the storage volume with a generated
key; downloads go through the tenant-scoped repository, never through a path from the client.
"""

import re
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError, RateLimitedError
from app.db.repository import (
    FINISHED_RUN_STATES,
    AnalysisRunRepository,
    DatasetRepository,
    DatasetVersionRepository,
    InsightRepository,
    ReportRepository,
)
from app.db.session import get_sessionmaker
from app.models.dataset import Dataset, DatasetVersion
from app.models.report import Report, ReportStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.services import run_summary, storage
from app.services.profiling.service import require_profile, resolve_version

logger = structlog.get_logger()

BUSY_RETRY_SECONDS = 10
SLUG_MAX = 60
ERROR_NO_ANALYSIS = "no_analysis"
ERROR_TOO_LARGE = "too_large"
ERROR_RENDER = "render_failed"
ERROR_STUCK = "timed_out"
ERROR_DISPATCH = "not_started"


# ---- request side ---------------------------------------------------------------------------


def start_report(
    db: Session,
    user: User,
    dataset: Dataset,
    version_number: int | None,
    locale: Literal["en", "ar"],
) -> Report:
    """Validate and queue a report; one runs at a time per company and the total is capped."""
    tenant_id = user.tenant_id
    version = resolve_version(db, dataset, version_number)
    require_profile(version)
    run = AnalysisRunRepository(db, tenant_id).latest_finished_for_version(version.id)
    if run is None:
        raise ConflictError(
            "This version has no completed analysis yet. Run the analysis first.",
            code="report_no_analysis",
        )
    # Lock the company row so two requests cannot both pass the one-at-a-time check.
    db.execute(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
    reports = ReportRepository(db, tenant_id)
    if reports.active() is not None:
        raise RateLimitedError(
            "A report is already being generated. Try again when it is ready.",
            code="report_busy",
            details={"retry_after_seconds": BUSY_RETRY_SECONDS},
            headers={"Retry-After": str(BUSY_RETRY_SECONDS)},
        )
    limit = get_settings().report_max_per_tenant
    if reports.count() >= limit:
        raise ConflictError(
            f"Your company keeps {limit} reports, the most allowed. Delete old ones first.",
            code="report_limit",
            details={"limit": limit},
        )
    report = reports.add(
        dataset_id=dataset.id,
        version=version.version,
        run_id=run.id,
        locale=locale,
        status=ReportStatus.queued.value,
        progress=0,
        title=dataset.name[:255],
        created_by=user.id,
    )
    db.commit()
    _dispatch(db, tenant_id, report)
    return report


def _dispatch(db: Session, tenant_id: uuid.UUID, report: Report) -> None:
    from app.workers.tasks import generate_report_task  # local import: tasks import this module

    report_id = report.id
    try:
        generate_report_task.delay(str(tenant_id), str(report_id))
        db.expire_all()  # an inline (eager) run updates the row through another session
    except Exception:
        logger.exception("report_dispatch_failed", report_id=str(report_id))
        db.rollback()
        failed = ReportRepository(db, tenant_id).get(report_id)
        if failed is not None:
            _fail(failed, ERROR_DISPATCH)
            db.commit()


def delete_report(db: Session, user: User, report_id: uuid.UUID) -> None:
    """Delete a report and its files (its creator, an owner or an admin)."""
    from app.core.errors import ForbiddenError

    repo = ReportRepository(db, user.tenant_id)
    report = repo.get_or_404(report_id)
    if report.created_by != user.id and user.role not in ("owner", "admin"):
        raise ForbiddenError(
            "Only the person who made a report, an owner or an admin may delete it."
        )
    if report.status in (ReportStatus.queued.value, ReportStatus.running.value):
        raise ConflictError("The report is still being generated.", code="report_running")
    _delete_files(report)
    repo.delete(report)
    db.commit()


def download(db: Session, tenant_id: uuid.UUID, report_id: uuid.UUID) -> tuple[Path, str]:
    """Return the PDF path and the Content-Disposition of a completed report of the tenant."""
    report = ReportRepository(db, tenant_id).get_or_404(report_id)
    backend = storage.get_storage()
    if report.status != ReportStatus.completed.value or not report.file_key:
        raise ConflictError("The report is not ready.", code="report_not_ready")
    if not backend.exists(report.file_key):
        raise ConflictError("The report file is missing; generate it again.", code="report_missing")
    return backend.local_path(report.file_key), content_disposition(report)


def filename(report: Report) -> str:
    """Return report-<dataset slug>-v<N>-<locale>-<yyyymmdd>.pdf (ASCII only)."""
    return _name(report, slug(report.title))


def _name(report: Report, label: str) -> str:
    stamp = (report.finished_at or report.created_at or datetime.now(UTC)).strftime("%Y%m%d")
    return f"report-{label}-v{int(report.version)}-{report.locale}-{stamp}.pdf"


def slug(text: str) -> str:
    """Return a lowercase ASCII slug of letters, digits and dashes ('dataset' when empty)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").lower()[:SLUG_MAX].strip("-")
    return cleaned or "dataset"


def readable(text: str) -> str:
    """Return the dataset name without control, path or quoting characters (for filename*)."""
    cleaned = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', "-", text).strip(" .-")[:SLUG_MAX]
    return cleaned.strip(" .-") or "dataset"


def content_disposition(report: Report) -> str:
    """Return an attachment header: an ASCII name, plus the UTF-8 name per RFC 5987."""
    utf8 = quote(_name(report, readable(report.title)), safe="")
    return f"attachment; filename=\"{filename(report)}\"; filename*=UTF-8''{utf8}"


# ---- worker side ----------------------------------------------------------------------------


class _NoAnalysis(Exception):
    """The run the report was queued for no longer exists or is not finished."""


def _fail(report: Report, code: str) -> None:
    report.status = ReportStatus.failed.value
    report.error_code = code
    report.finished_at = datetime.now(UTC)


def _progress(db: Session, report: Report, value: int) -> None:
    report.progress = value
    db.commit()


def generate_report(tenant_id: uuid.UUID, report_id: uuid.UUID) -> None:
    """Build and store the PDF of a queued report (worker entry point)."""
    from app.reports.render import ReportTooLarge

    with get_sessionmaker()() as db:
        report = ReportRepository(db, tenant_id).get(report_id)
        if report is None or report.status != ReportStatus.queued.value:
            return
        report.status = ReportStatus.running.value
        _progress(db, report, 10)
        try:
            _build(db, tenant_id, report)
        except _NoAnalysis:
            db.rollback()
            _fail(report, ERROR_NO_ANALYSIS)
        except ReportTooLarge:
            db.rollback()
            _fail(report, ERROR_TOO_LARGE)
        except Exception:
            logger.exception("report_failed", report_id=str(report_id))
            db.rollback()
            _fail(report, ERROR_RENDER)
        db.commit()
        logger.info("report_done", report_id=str(report_id), status=report.status)


def _build(db: Session, tenant_id: uuid.UUID, report: Report) -> None:
    from app.reports.facts import build_report_facts
    from app.reports.render import render_pdf

    dataset = DatasetRepository(db, tenant_id).get(report.dataset_id)
    run = AnalysisRunRepository(db, tenant_id).get(report.run_id) if report.run_id else None
    if dataset is None or run is None or run.status not in FINISHED_RUN_STATES:
        raise _NoAnalysis
    version = DatasetVersionRepository(db, tenant_id).get(run.dataset_version_id)
    if version is None:
        raise _NoAnalysis
    if run.summary is None:
        run.summary = summary_for(db, tenant_id, version, run.id)
    facts = build_report_facts(
        db,
        tenant_id,
        report_id=report.id,
        dataset=dataset,
        version=version,
        run=run,
        locale=report.locale,  # type: ignore[arg-type]
        now=datetime.now(UTC),
    )
    _progress(db, report, 40)
    settings = get_settings()
    pdf = render_pdf(facts, int(settings.report_max_mb * 1024 * 1024))
    _progress(db, report, 80)
    backend = storage.get_storage()
    key = storage.report_key(tenant_id)
    backend.save_bytes(key[: -len(".pdf")] + ".json", facts.model_dump_json(indent=1).encode())
    report.size_bytes = backend.save_bytes(key, pdf)
    report.file_key = key
    report.status = ReportStatus.completed.value
    report.progress = 100
    report.finished_at = datetime.now(UTC)


def summary_for(
    db: Session, tenant_id: uuid.UUID, version: DatasetVersion, run_id: uuid.UUID
) -> dict[str, Any] | None:
    """Build the summary of a run that finished before summaries existed (from stored insights)."""
    from app.services.analysis import analysis_context

    insights = InsightRepository(db, tenant_id).for_run(run_id, limit=500)
    found = [{"type": i.type, "payload": i.payload, "chart_spec": i.chart_spec} for i in insights]
    run = AnalysisRunRepository(db, tenant_id).get_or_404(run_id)
    with analysis_context(db, tenant_id, version, dict(run.config)) as context:
        return run_summary.build(context, version, found)


def facts_key(report: Report) -> str | None:
    """Return the storage key of the facts JSON saved next to a report's PDF."""
    if not report.file_key:
        return None
    return report.file_key[: -len(".pdf")] + ".json"


def _delete_files(report: Report) -> None:
    backend = storage.get_storage()
    for key in (report.file_key, facts_key(report)):
        if key:
            backend.delete(key)


def fail_stuck_reports(db: Session, now: datetime | None = None) -> int:
    """Fail reports queued or running for longer than REPORT_STUCK_MINUTES (beat)."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=get_settings().report_stuck_minutes)
    stuck = db.scalars(
        select(Report).where(
            Report.status.in_([ReportStatus.queued.value, ReportStatus.running.value]),
            Report.created_at < cutoff,
        )
    ).all()
    for report in stuck:
        _fail(report, ERROR_STUCK)
    db.commit()
    return len(stuck)


def purge_expired_reports(db: Session, now: datetime | None = None) -> int:
    """Delete reports (and their files) older than REPORT_RETENTION_DAYS (beat)."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=get_settings().report_retention_days)
    old = db.scalars(
        select(Report).where(
            Report.created_at < cutoff,
            Report.status.in_([ReportStatus.completed.value, ReportStatus.failed.value]),
        )
    ).all()
    for report in old:
        _delete_files(report)
        db.delete(report)
    db.commit()
    return len(old)
