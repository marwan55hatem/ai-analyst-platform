"""Dashboard time series endpoint: KPIs for a date range, the window before it and a series."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api import responses
from app.api.deps import CurrentUser, DatasetRepo, DbSession
from app.ml.timeseries import Grain
from app.schemas.timeseries import TimeSeriesOut
from app.services import timeseries
from app.services.profiling import service as profiling

Day = Annotated[date | None, Query(description="Inclusive date (default: the edge of the data)")]

router = APIRouter(
    tags=["dashboard"],
    responses={**responses.UNAUTHORIZED, **responses.NOT_FOUND},
)


@router.get(
    "/datasets/{dataset_id}/timeseries",
    response_model=TimeSeriesOut,
    summary="Headline KPIs for a date range vs the window before it, and a revenue series",
    responses={**responses.CONFLICT, **responses.UNPROCESSABLE},
)
def get_time_series(
    dataset_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    repo: DatasetRepo,
    version: Annotated[int | None, Query(ge=1)] = None,
    start: Day = None,
    end: Day = None,
    grain: Grain = "day",
) -> TimeSeriesOut:
    """Aggregate the version's rows inside the range; the SQL is returned as evidence."""
    target = profiling.resolve_version(db, repo.get_or_404(dataset_id), version)
    found = timeseries.time_series(db, user.tenant_id, target, start=start, end=end, grain=grain)
    return TimeSeriesOut.model_validate(found)
