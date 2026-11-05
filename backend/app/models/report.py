"""Generated PDF reports, in-app alerts and each company's alert thresholds."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin

REPORT_LOCALES = ("en", "ar")
ALERT_SEVERITIES = ("info", "warning", "critical")


class ReportStatus(StrEnum):
    """Lifecycle of a report."""

    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class Report(TenantMixin, Base):
    """One PDF built from the stored results of one analysis run."""

    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint(f"status in ({_in(tuple(s.value for s in ReportStatus))})", name="status"),
        CheckConstraint(f"locale in ({_in(REPORT_LOCALES)})", name="locale"),
        Index("ix_reports_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True
    )
    locale: Mapped[str] = mapped_column(String(5))
    status: Mapped[str] = mapped_column(String(20), default=ReportStatus.queued.value)
    progress: Mapped[int] = mapped_column(SmallInteger, default=0)
    title: Mapped[str] = mapped_column(String(255))
    file_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Alert(TenantMixin, Base):
    """A plain-language notice that a new analysis run differs from the previous one."""

    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(f"severity in ({_in(ALERT_SEVERITIES)})", name="severity"),
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_alerts_tenant_dedupe"),
        Index("ix_alerts_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True
    )
    rule: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AlertSettings(TenantMixin, Base):
    """A company's alert thresholds; a missing row means the defaults from the settings."""

    __tablename__ = "alert_settings"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_alert_settings_tenant"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    revenue_drop_pct: Mapped[float] = mapped_column(Float)
    orders_drop_pct: Mapped[float] = mapped_column(Float)
    churn_increase_pct: Mapped[float] = mapped_column(Float)
    quality_drop_points: Mapped[int] = mapped_column(Integer)
    freshness_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    freshness_days: Mapped[int] = mapped_column(Integer)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
