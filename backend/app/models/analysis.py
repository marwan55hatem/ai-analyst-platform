"""Analysis runs and the insights they produce."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin

CONFIDENCE_LEVELS = ("high", "medium", "low")


class AnalysisStatus(StrEnum):
    """Lifecycle of an analysis run."""

    pending = "pending"
    running = "running"
    completed = "completed"
    completed_with_warnings = "completed_with_warnings"
    failed = "failed"


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class AnalysisRun(TenantMixin, Base):
    """One execution of the analysis pipeline over one dataset version."""

    __tablename__ = "analysis_runs"
    __table_args__ = (
        CheckConstraint(
            f"status in ({_in(tuple(s.value for s in AnalysisStatus))})", name="status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default=AnalysisStatus.pending.value)
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Insight(TenantMixin, Base):
    """A ranked, plain-language finding with the evidence behind it."""

    __tablename__ = "insights"
    __table_args__ = (
        CheckConstraint(f"confidence in ({_in(CONFIDENCE_LEVELS)})", name="confidence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True
    )
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), index=True
    )
    module: Mapped[str] = mapped_column(String(50))
    type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    impact_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[str] = mapped_column(String(10))
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    chart_spec: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
