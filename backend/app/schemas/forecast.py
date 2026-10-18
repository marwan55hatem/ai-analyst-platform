"""Schemas of the forecast endpoints."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.analysis import InsightOut

MAX_HORIZON_HARD_LIMIT = 120


class ForecastRequest(BaseModel):
    """What to forecast. Limits that depend on settings are checked by the service (422)."""

    metric: Literal["revenue", "quantity", "cost"] = "revenue"
    horizon: int = Field(default=6, ge=1, le=MAX_HORIZON_HARD_LIMIT, description="Periods ahead")
    group_by: str | None = Field(
        default=None,
        max_length=200,
        description="A dimension role (product, category, region, channel) or its column name",
    )
    max_groups: int = Field(default=5, ge=1, le=100, description="Largest groups to forecast")
    frequency: Literal["day", "week", "month", "quarter"] | None = Field(
        default=None, description="Aggregation level; default is chosen from the data"
    )
    date_column: str | None = Field(default=None, max_length=200)


class ForecastRunOut(BaseModel):
    """Status of a custom forecast; poll it until `status` is completed or failed."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_version_id: uuid.UUID
    kind: str
    status: str
    config: dict[str, Any]
    result: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    created_at: datetime


class RunForecastOut(BaseModel):
    """The forecast insights of an analysis run."""

    run_id: uuid.UUID
    module_status: str
    forecasts: list[InsightOut]
    notes: list[str] = []
