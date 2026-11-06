"""Schemas of the alert endpoints."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AlertOut(BaseModel):
    """One alert. `payload` carries the compared numbers, the periods and a link target."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    run_id: uuid.UUID | None
    rule: str
    severity: Literal["info", "warning", "critical"]
    title: str
    body: str
    payload: dict[str, Any]
    created_at: datetime
    read_at: datetime | None


class AlertPage(BaseModel):
    """One page of alerts, newest first."""

    items: list[AlertOut]
    total: int


class UnreadCount(BaseModel):
    """How many alerts are unread."""

    count: int


class ReadAllOut(BaseModel):
    """How many alerts were marked read."""

    updated: int


class AlertSettingsIn(BaseModel):
    """New thresholds (owner or admin). Every value has sane bounds."""

    revenue_drop_pct: float = Field(ge=1, le=90, description="Month-over-month fallback, percent")
    orders_drop_pct: float = Field(ge=1, le=90, description="Month-over-month, percent")
    churn_increase_pct: float = Field(ge=1, le=500, description="Rise in high-risk churn, percent")
    quality_drop_points: int = Field(ge=1, le=50, description="Fall in the 0-100 quality score")
    freshness_enabled: bool = Field(description="Daily 'no new data' check")
    freshness_days: int = Field(ge=1, le=365, description="Days without new data before alerting")


class AlertSettingsOut(AlertSettingsIn):
    """Current thresholds; `is_default` is true while the company has saved none."""

    is_default: bool
    can_edit: bool
    updated_at: datetime | None
