"""Schemas of the analysis pipeline: capabilities, runs and insights."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ml.format import LOCALES

Confidence = Literal["high", "medium", "low"]


class AnalyzeRequest(BaseModel):
    """Optional settings for a run; anything left out keeps the last run's value or the default."""

    date_column: str | None = Field(default=None, max_length=200)
    currency: str | None = Field(default=None, min_length=1, max_length=8)
    locale: str | None = Field(default=None, pattern="^(" + "|".join(LOCALES) + ")$")
    dead_stock_days: int | None = Field(default=None, ge=7, le=730)


class CapabilityOut(BaseModel):
    """Whether one analysis module can run, and why not when it cannot."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    title: str
    available: bool
    reason: str | None
    notes: list[str]


class CapabilitiesOut(BaseModel):
    """What a dataset version supports."""

    version_id: uuid.UUID
    version: int
    roles: dict[str, Any]
    modules: list[CapabilityOut]


class AnalysisRunOut(BaseModel):
    """Status and per-module progress of an analysis run."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_version_id: uuid.UUID
    status: str
    progress: dict[str, Any]
    config: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    created_at: datetime


class AnalysisRunListItem(BaseModel):
    """One run in a dataset's run list."""

    id: uuid.UUID
    dataset_version_id: uuid.UUID
    version: int
    status: str
    progress: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    insight_count: int


class InsightSummary(BaseModel):
    """An insight without its heavy evidence, for lists."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    module: str
    type: str
    title: str
    summary: str
    impact_score: float
    confidence: Confidence
    sample_size: int
    chart_spec: dict[str, Any]
    created_at: datetime


class InsightOut(InsightSummary):
    """A full insight: numbers, chart spec and the SQL evidence."""

    dataset_version_id: uuid.UUID
    payload: dict[str, Any]
    evidence: dict[str, Any]
