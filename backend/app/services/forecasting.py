"""Custom forecasts: validate the request, queue it, and compute it in the Celery worker."""

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError, UnprocessableError
from app.db.repository import DatasetVersionRepository, ModelRunRepository
from app.db.session import get_sessionmaker
from app.ml.base import Insight
from app.ml.modules.common import jsonable
from app.ml.modules.forecast import ForecastModule, resolve_group_column
from app.models.dataset import DatasetVersion
from app.models.model_run import ModelRun
from app.schemas.forecast import ForecastRequest
from app.services.analysis import _roles_for, analysis_context, merged_config
from app.services.profiling.service import require_profile

logger = structlog.get_logger()

FORECAST_FAILED_MESSAGE = "The forecast failed. Please try again."
FORECAST_START_FAILED = "The forecast could not be started. Please try again later."


def validate_request(
    db: Session, tenant_id: uuid.UUID, version: DatasetVersion, request: ForecastRequest
) -> dict[str, Any]:
    """Check a request against the dataset and the configured limits; return the module config."""
    settings = get_settings()
    require_profile(version)
    base = merged_config(db, tenant_id, version, {})
    date_column = request.date_column or base.get("date_column")
    roles = _roles_for(db, tenant_id, version, date_column)
    if not roles.has("date"):
        raise UnprocessableError(
            "This dataset has no date column to forecast over.", code="no_date"
        )
    if not roles.has(request.metric):
        raise UnprocessableError(
            f"This dataset has no {request.metric} column to forecast.", code="metric_unavailable"
        )
    if request.horizon > settings.forecast_max_horizon:
        raise UnprocessableError(
            f"The horizon is limited to {settings.forecast_max_horizon} periods.",
            code="horizon_too_large",
        )
    if request.max_groups > settings.forecast_max_groups:
        raise UnprocessableError(
            f"At most {settings.forecast_max_groups} groups can be forecast at once.",
            code="too_many_groups",
        )
    if request.group_by:
        try:
            resolve_group_column(roles, request.group_by)
        except ValueError as exc:
            raise UnprocessableError(str(exc), code="invalid_group_by") from exc
    config: dict[str, Any] = {
        "metric": request.metric,
        "horizon": request.horizon,
        "max_groups": request.max_groups,
        "forecast_custom": True,
    }
    for key in ("currency", "locale"):
        if base.get(key):
            config[key] = base[key]
    if date_column:
        config["date_column"] = date_column
    if request.group_by:
        config["group_by"] = request.group_by
    if request.frequency:
        config["frequency"] = request.frequency
    return config


def start_forecast(
    db: Session, tenant_id: uuid.UUID, version: DatasetVersion, request: ForecastRequest
) -> ModelRun:
    """Queue a custom forecast; the computation happens in the worker, never in the request."""
    config = validate_request(db, tenant_id, version, request)
    locked = DatasetVersionRepository(db, tenant_id).get_or_404(version.id, for_update=True)
    runs = ModelRunRepository(db, tenant_id)
    if runs.active_for_version(locked.id, "forecast") is not None:
        raise ConflictError(
            "A forecast is already running for this version.", code="forecast_running"
        )
    run = runs.add(
        dataset_version_id=locked.id, kind="forecast", status="pending", config=config, result={}
    )
    db.commit()
    _dispatch(db, tenant_id, run.id)
    return run


def _dispatch(db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> None:
    from app.workers.tasks import run_forecast_task  # local import: tasks import this module

    try:
        run_forecast_task.delay(str(tenant_id), str(run_id))
        db.expire_all()  # an inline (eager) run updates rows through another session
    except Exception:
        logger.exception("forecast_dispatch_failed", run_id=str(run_id))
        db.rollback()
        failed = ModelRunRepository(db, tenant_id).get(run_id)
        if failed is not None:
            failed.status = "failed"
            failed.error = FORECAST_START_FAILED
            failed.finished_at = datetime.now(UTC)
            db.commit()


def run_forecast(tenant_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Compute a pending custom forecast (worker entry point)."""
    with get_sessionmaker()() as db:
        run = ModelRunRepository(db, tenant_id).get(run_id)
        if run is None or run.status != "pending":
            return
        run.status = "running"
        run.started_at = datetime.now(UTC)
        db.commit()
        try:
            _execute(db, tenant_id, run_id)
        except Exception:
            logger.exception("forecast_failed", run_id=str(run_id))
            db.rollback()
            failed = ModelRunRepository(db, tenant_id).get(run_id)
            if failed is not None:
                failed.status = "failed"
                failed.error = FORECAST_FAILED_MESSAGE
                failed.finished_at = datetime.now(UTC)
                db.commit()


def _execute(db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> None:
    run = ModelRunRepository(db, tenant_id).get_or_404(run_id)
    version = DatasetVersionRepository(db, tenant_id).get_or_404(run.dataset_version_id)
    with analysis_context(db, tenant_id, version, dict(run.config)) as context:
        insights = ForecastModule().run(context)
        warnings = list(context.warnings)
    run.result = jsonable({"insights": [_stored(i) for i in insights], "warnings": warnings})
    run.status = "completed"
    run.finished_at = datetime.now(UTC)
    db.commit()
    logger.info("forecast_done", run_id=str(run.id), series=len(insights))


def _stored(insight: Insight) -> dict[str, Any]:
    return {
        "module": insight.module,
        "type": insight.type,
        "title": insight.title,
        "summary": insight.summary,
        "confidence": insight.result.confidence,
        "sample_size": insight.result.sample_size,
        "impact_score": insight.impact_score,
        "payload": insight.stored_payload(),
        "chart_spec": insight.chart_spec,
        "evidence": insight.evidence,
    }
