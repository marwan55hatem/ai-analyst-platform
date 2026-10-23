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


class AnomaliesOut(BaseModel):
    """A run's anomalies: unusual periods with likely contributors, level shifts and odd rows."""

    run_id: uuid.UUID
    status: str
    module_status: str | None = Field(
        description="completed, skipped or failed for the anomalies module (null until it ran)"
    )
    skipped_reason: str | None
    warnings: list[str]
    unit: str | None = Field(description="day, week or month")
    events: list[dict[str, Any]] = Field(
        description="Every anomaly, largest first; findings from data, not proven causes"
    )
    level_shifts: list[dict[str, Any]]
    row_anomalies: dict[str, Any] | None
    insights: list[InsightOut]


class SegmentAssignmentOut(BaseModel):
    """One customer's RFM segment and cluster (a pseudonym when the id is personal data)."""

    model_config = ConfigDict(from_attributes=True)

    customer_key: str
    rfm_segment: str
    r_score: int
    f_score: int
    m_score: int
    recency_days: int
    frequency: int
    monetary: float
    cluster: int | None
    cluster_name: str | None


class AssignmentPage(BaseModel):
    """One page of customer assignments."""

    items: list[SegmentAssignmentOut]
    total: int
    limit: int
    offset: int


class SegmentsOut(BaseModel):
    """A run's customer segments: RFM table, cluster profiles, stability and assignments."""

    run_id: uuid.UUID
    status: str
    module_status: str | None
    skipped_reason: str | None
    warnings: list[str]
    pseudonymized_customer_ids: bool
    segments: list[dict[str, Any]] = Field(
        description="RFM segments; each recommended_action is a rule-based suggestion"
    )
    clusters: list[dict[str, Any]]
    k: int | None
    stability: dict[str, Any] | None
    assignments: AssignmentPage
    insights: list[InsightOut]


class ReasonOut(BaseModel):
    """One reason behind a customer's estimated risk (SHAP contribution of a feature)."""

    feature: str
    value: float
    typical: float
    effect: Literal["raises", "lowers"]
    shap: float
    text: str


class ChurnScoreOut(BaseModel):
    """One customer's estimated churn risk (a pseudonym when the id is personal data)."""

    model_config = ConfigDict(from_attributes=True)

    customer_key: str
    probability: float = Field(description="Calibrated estimated probability, kept in 0.5-99.5%")
    risk_band: Literal["high", "medium", "low"]
    rank_band: Literal["top_10pct", "rest"]
    revenue_at_stake: float = Field(description="Yearly spend: spend x 365 / max(tenure, 365)")
    expected_loss: float = Field(description="probability x revenue_at_stake")
    recency_days: int
    frequency: int
    monetary: float
    reasons: list[ReasonOut] = Field(description="Top 3 reasons, largest SHAP effect first")


class ChurnPage(BaseModel):
    """One page of customers, highest estimated risk first."""

    items: list[ChurnScoreOut]
    total: int
    limit: int
    offset: int


class ChurnOut(BaseModel):
    """A run's churn risk: model check, risk bands, drivers and a page of customers."""

    run_id: uuid.UUID
    status: str
    module_status: str | None
    skipped_reason: str | None
    warnings: list[str]
    pseudonymized_customer_ids: bool
    basis: Literal["probability", "rank"] | None = Field(
        description="Which view leads the text: probability bands, or the top-10% rank"
    )
    bands: dict[str, Any]
    importance: list[dict[str, Any]]
    model: dict[str, Any] | None
    customers: ChurnPage
    insights: list[InsightOut]


class DriversOut(BaseModel):
    """A run's revenue drivers; they describe association, not proven cause."""

    run_id: uuid.UUID
    status: str
    module_status: str | None
    skipped_reason: str | None
    warnings: list[str]
    importance: list[dict[str, Any]]
    partial_dependence: dict[str, list[dict[str, Any]]]
    r2_holdout: float | None
    methods_agree_on_top: bool | None
    association_not_causation: bool
    insights: list[InsightOut]
