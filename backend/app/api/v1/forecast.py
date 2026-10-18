"""Forecast endpoints: the automatic forecast of an analysis run and custom async forecasts."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api import responses
from app.api.deps import CurrentUser, DatasetRepo, DbSession
from app.db.repository import AnalysisRunRepository, InsightRepository, ModelRunRepository
from app.schemas.analysis import InsightOut
from app.schemas.forecast import ForecastRequest, ForecastRunOut, RunForecastOut
from app.services import forecasting
from app.services.profiling import service as profiling

router = APIRouter(
    tags=["forecast"],
    responses={**responses.UNAUTHORIZED, **responses.NOT_FOUND},
)


@router.get(
    "/analysis-runs/{run_id}/forecast",
    response_model=RunForecastOut,
    summary="The forecasts of an analysis run (total and top groups, with 80% and 95% ranges)",
)
def get_run_forecast(run_id: uuid.UUID, user: CurrentUser, db: DbSession) -> RunForecastOut:
    """Return the forecast insights a run produced, or why there are none."""
    run = AnalysisRunRepository(db, user.tenant_id).get_or_404(run_id)
    state = (run.progress or {}).get("modules", {}).get("forecast", {})
    found = InsightRepository(db, user.tenant_id).for_run(run_id, module="forecast", limit=200)
    notes = [str(w) for w in state.get("warnings", [])]
    if state.get("reason"):
        notes.append(str(state["reason"]))
    return RunForecastOut(
        run_id=run_id,
        module_status=str(state.get("status", "pending")),
        forecasts=[InsightOut.model_validate(i) for i in found],
        notes=notes,
    )


@router.post(
    "/datasets/{dataset_id}/forecast",
    response_model=ForecastRunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a custom forecast (metric, horizon, group_by); poll /forecast-runs/{id}",
    responses={**responses.CONFLICT, **responses.UNPROCESSABLE},
)
def create_forecast(
    dataset_id: uuid.UUID,
    body: ForecastRequest,
    user: CurrentUser,
    db: DbSession,
    repo: DatasetRepo,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> ForecastRunOut:
    """Validate the request and queue it; the worker computes the forecast."""
    target = profiling.resolve_version(db, repo.get_or_404(dataset_id), version)
    run = forecasting.start_forecast(db, user.tenant_id, target, body)
    db.refresh(run)
    return ForecastRunOut.model_validate(run)


@router.get(
    "/forecast-runs/{forecast_run_id}",
    response_model=ForecastRunOut,
    summary="Status and result of a custom forecast",
)
def get_forecast_run(
    forecast_run_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ForecastRunOut:
    """Return one custom forecast of the caller's tenant."""
    run = ModelRunRepository(db, user.tenant_id).get_or_404(forecast_run_id)
    return ForecastRunOut.model_validate(run)
