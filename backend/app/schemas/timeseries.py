"""Dashboard time series: KPIs for a date range vs the window before it, and a filled series."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.analysis import Confidence


class DateWindow(BaseModel):
    """An inclusive date range (ISO dates)."""

    start: str
    end: str


class KpiOut(BaseModel):
    """One headline figure for the range and its change vs the window before it."""

    key: Literal["net_revenue", "orders", "aov", "units", "margin_pct"]
    unit: Literal["money", "count", "percent"]
    available: bool = Field(description="False when the file lacks the column this needs")
    value: float | None
    previous_value: float | None
    change: float | None = Field(
        description="Relative change in percent, or percentage points for margin; null with a "
        "reason when there is nothing to compare with"
    )
    change_unit: Literal["percent", "points"]
    reason: str | None


class SeriesPoint(BaseModel):
    """One period of the series; periods without rows are zero."""

    period: str
    net_revenue: float
    orders: float
    aov: float | None
    units: float | None
    margin_pct: float | None
    rows: int
    partial: bool = Field(description="The period is only partly inside the selected range")


class EvidenceOut(BaseModel):
    """The SQL behind the figures and the first rows of its result."""

    purpose: str
    sql: str
    columns: list[str]
    rows: list[list[object]]
    row_count: int


class TimeSeriesOut(BaseModel):
    """KPIs, comparison window and series for one dataset version and date range."""

    dataset_id: uuid.UUID
    dataset_version_id: uuid.UUID
    version: int
    range: DateWindow
    data_range: DateWindow
    previous_range: DateWindow
    grain: Literal["day", "week", "month"]
    currency: str | None
    kpis: list[KpiOut]
    series: list[SeriesPoint]
    sample_size: int
    confidence: Confidence
    method: str
    warnings: list[str]
    evidence: list[EvidenceOut]
