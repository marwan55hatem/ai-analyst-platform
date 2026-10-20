"""Analysis use cases: capabilities, start or re-run a run, and the module-running worker."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError
from app.db.repository import (
    AnalysisRunRepository,
    ColumnProfileRepository,
    DatasetVersionRepository,
    InsightRepository,
    SegmentAssignmentRepository,
)
from app.db.session import get_sessionmaker
from app.ml import capabilities, registry
from app.ml.base import AnalysisContext, DataFacts, Insight
from app.ml.engine import open_analysis
from app.ml.modules.segments import CACHE_KEY as SEGMENT_CACHE_KEY
from app.ml.quality import QualityContext
from app.ml.roles import RoleMap
from app.models.analysis import AnalysisRun, AnalysisStatus
from app.models.dataset import DatasetVersion
from app.services import storage
from app.services.profiling.service import require_profile

logger = structlog.get_logger()

ANALYSIS_FAILED_MESSAGE = "The analysis failed. Please try running it again."
ANALYSIS_START_FAILED = "The analysis could not be started. Please try again later."
ANALYSIS_STUCK_ERROR = "The analysis timed out. Please run it again."
MODULE_ERROR_LIMIT = 300
SEGMENT_MODULE = "segmentation"


# ---- request side --------------------------------------------------------------------------


def _roles_for(
    db: Session, tenant_id: uuid.UUID, version: DatasetVersion, date_column: str | None = None
) -> RoleMap:
    profiles = ColumnProfileRepository(db, tenant_id).for_version(version.id)
    order = [m["normalized"] for m in (version.column_map or [])]
    return RoleMap.from_profiles(profiles, order, date_column)


def capabilities_payload(
    db: Session, tenant_id: uuid.UUID, version: DatasetVersion, date_column: str | None = None
) -> dict[str, Any]:
    """Return which modules can run on a version and why the others cannot."""
    require_profile(version)
    roles = _roles_for(db, tenant_id, version, date_column)
    profiles = ColumnProfileRepository(db, tenant_id).for_version(version.id)
    facts = DataFacts.from_profiles(roles, profiles, version.dataset_profile)
    caps = capabilities.detect(registry.all_modules(), roles, facts)
    return {
        "version_id": version.id,
        "version": version.version,
        "roles": roles.as_dict(),
        "modules": caps,
    }


def _merged_config(
    db: Session, tenant_id: uuid.UUID, version: DatasetVersion, explicit: dict[str, Any]
) -> dict[str, Any]:
    """Explicit settings win; anything left out is inherited from the version's last run."""
    previous = AnalysisRunRepository(db, tenant_id).latest_for_version(version.id)
    inherited = {} if previous is None else dict(previous.config)
    inherited.pop("auto", None)
    return {**inherited, **explicit}


def start_analysis(
    db: Session, tenant_id: uuid.UUID, version: DatasetVersion, explicit: dict[str, Any]
) -> AnalysisRun:
    """Validate and queue a (re-)run for a version with a ready profile."""
    require_profile(version)
    config = _merged_config(db, tenant_id, version, explicit)
    _roles_for(db, tenant_id, version, config.get("date_column"))  # 422 for a bad date column
    locked = DatasetVersionRepository(db, tenant_id).get_or_404(version.id, for_update=True)
    if AnalysisRunRepository(db, tenant_id).active_for_version(locked.id) is not None:
        raise ConflictError(
            "An analysis is already running for this version.", code="analysis_running"
        )
    run = AnalysisRunRepository(db, tenant_id).add(
        dataset_version_id=locked.id,
        status=AnalysisStatus.pending.value,
        config=config,
        progress={},
    )
    db.commit()
    _dispatch(db, tenant_id, run)
    return run


def enqueue_analysis(db: Session, tenant_id: uuid.UUID, version: DatasetVersion) -> None:
    """Queue the automatic run after a profile becomes ready; never raises."""
    try:
        if AnalysisRunRepository(db, tenant_id).active_for_version(version.id) is not None:
            return
        config = _merged_config(db, tenant_id, version, {})
        run = AnalysisRunRepository(db, tenant_id).add(
            dataset_version_id=version.id,
            status=AnalysisStatus.pending.value,
            config={**config, "auto": True},
            progress={},
        )
        db.commit()
        _dispatch(db, tenant_id, run)
    except Exception:
        logger.exception("analysis_enqueue_failed", version_id=str(version.id))
        db.rollback()


def _dispatch(db: Session, tenant_id: uuid.UUID, run: AnalysisRun) -> None:
    from app.workers.tasks import run_analysis_task  # local import: tasks import this module

    run_id = run.id
    try:
        run_analysis_task.delay(str(tenant_id), str(run_id))
        db.expire_all()  # an inline (eager) run updates rows through another session
    except Exception:
        logger.exception("analysis_dispatch_failed", run_id=str(run_id))
        db.rollback()
        failed = AnalysisRunRepository(db, tenant_id).get(run_id)
        if failed is not None:
            failed.status = AnalysisStatus.failed.value
            failed.error = ANALYSIS_START_FAILED
            failed.finished_at = datetime.now(UTC)
            db.commit()


# ---- worker side ---------------------------------------------------------------------------


def run_analysis(tenant_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Execute every supported module for a pending run (worker entry point)."""
    with get_sessionmaker()() as db:
        run = AnalysisRunRepository(db, tenant_id).get(run_id)
        if run is None or run.status != AnalysisStatus.pending.value:
            return
        run.status = AnalysisStatus.running.value
        run.started_at = datetime.now(UTC)
        db.commit()
        try:
            _execute(db, tenant_id, run_id)
        except Exception:
            logger.exception("analysis_failed", run_id=str(run_id))
            db.rollback()
            failed = AnalysisRunRepository(db, tenant_id).get(run_id)
            if failed is not None:
                failed.status = AnalysisStatus.failed.value
                failed.error = ANALYSIS_FAILED_MESSAGE
                failed.finished_at = datetime.now(UTC)
                db.commit()


@contextmanager
def analysis_context(
    db: Session, tenant_id: uuid.UUID, version: DatasetVersion, config: dict[str, Any]
) -> Iterator[AnalysisContext]:
    """Open the hardened per-run DuckDB table and yield the context modules receive."""
    assert version.parquet_path is not None
    profiles = ColumnProfileRepository(db, tenant_id).for_version(version.id)
    order = [m["normalized"] for m in (version.column_map or [])]
    roles = RoleMap.from_profiles(profiles, order, config.get("date_column"))
    date_name = roles.col("date")
    granularity = next((p.stats.get("granularity") for p in profiles if p.name == date_name), None)
    parquet = storage.get_storage().local_path(version.parquet_path)
    with open_analysis(parquet, roles) as connection:
        yield AnalysisContext(
            tenant_id=tenant_id,
            version_id=version.id,
            roles=roles,
            db=connection,
            settings=get_settings(),
            quality=QualityContext.from_version(
                version.dataset_profile, version.quality_score, profiles
            ),
            config=dict(config),
            granularity=granularity,
            facts=DataFacts.from_profiles(roles, profiles, version.dataset_profile),
            version_number=version.version,
        )


def _execute(db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> None:
    runs = AnalysisRunRepository(db, tenant_id)
    run = runs.get_or_404(run_id)
    version = DatasetVersionRepository(db, tenant_id).get_or_404(run.dataset_version_id)
    modules = registry.all_modules()
    progress: dict[str, Any] = {"total": len(modules), "done": 0, "modules": {}}
    collected: list[Insight] = []
    with analysis_context(db, tenant_id, version, dict(run.config)) as context:
        for module in modules:
            progress["modules"][module.name] = _run_module(module, context, collected)
            progress["done"] += 1
            run.progress = dict(progress)
            db.commit()
        _store_assignments(db, tenant_id, run, version.id, context, progress, collected)
    run.progress = dict(progress)
    states = [m["status"] for m in progress["modules"].values()]
    InsightRepository(db, tenant_id).delete_for_version(version.id)
    repo = InsightRepository(db, tenant_id)
    for insight in collected:
        repo.add(
            run_id=run.id,
            dataset_version_id=version.id,
            module=insight.module,
            type=insight.type,
            title=insight.title,
            summary=insight.summary,
            impact_score=insight.impact_score,
            confidence=insight.result.confidence,
            sample_size=insight.result.sample_size,
            payload=insight.stored_payload(),
            chart_spec=insight.chart_spec,
            evidence=insight.evidence,
        )
    if "failed" in states and "completed" not in states:
        run.status = AnalysisStatus.failed.value
        run.error = ANALYSIS_FAILED_MESSAGE
    elif "failed" in states or "skipped" in states:
        run.status = AnalysisStatus.completed_with_warnings.value
    else:
        run.status = AnalysisStatus.completed.value
    run.finished_at = datetime.now(UTC)
    db.commit()
    logger.info("analysis_done", run_id=str(run.id), status=run.status, insights=len(collected))


def _store_assignments(
    db: Session,
    tenant_id: uuid.UUID,
    run: AnalysisRun,
    version_id: uuid.UUID,
    context: AnalysisContext,
    progress: dict[str, Any],
    collected: list[Insight],
) -> None:
    """Replace the version's per-customer segments while the DuckDB connection is still open."""
    repo = SegmentAssignmentRepository(db, tenant_id)
    repo.delete_for_version(version_id)
    make = context.cache.get(SEGMENT_CACHE_KEY)
    state = progress["modules"].get(SEGMENT_MODULE, {})
    if make is None or state.get("status") != "completed":
        return
    savepoint = db.begin_nested()
    try:
        for batch in make():
            repo.add_many(run.id, version_id, batch)
        savepoint.commit()
    except Exception as exc:
        logger.exception("segment_assignments_failed", run_id=str(run.id))
        savepoint.rollback()
        message = f"{type(exc).__name__}: {exc}"[:MODULE_ERROR_LIMIT]
        collected[:] = [i for i in collected if i.module != SEGMENT_MODULE]
        progress["modules"][SEGMENT_MODULE] = {
            "status": "failed",
            "error": message,
            "insights": 0,
            "warnings": [f"Saving the customer segments failed: {message}"],
        }


def _run_module(module: Any, context: AnalysisContext, collected: list[Insight]) -> dict[str, Any]:
    """Run one module; a failure becomes a warning on the run and the others carry on."""
    capability = capabilities.check(module, context.roles, context.facts)
    if not capability.available:
        return {"status": "skipped", "reason": capability.reason, "insights": 0, "warnings": []}
    context.warnings.clear()
    try:
        found = module.run(context)
    except Exception as exc:
        logger.exception("analysis_module_failed", module=module.name)
        message = f"{type(exc).__name__}: {exc}"[:MODULE_ERROR_LIMIT]
        return {
            "status": "failed",
            "error": message,
            "insights": 0,
            "warnings": [f"The {module.title.lower()} module failed and was skipped: {message}"],
        }
    collected.extend(found)
    return {
        "status": "completed",
        "insights": len(found),
        "warnings": list(context.warnings),
        "notes": capability.notes,
    }
