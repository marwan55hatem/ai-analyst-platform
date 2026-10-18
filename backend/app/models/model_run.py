"""Custom model runs (forecasts requested through the API) and their stored results."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin

MODEL_RUN_KINDS = ("forecast",)
MODEL_RUN_STATUSES = ("pending", "running", "completed", "failed")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class ModelRun(TenantMixin, Base):
    """One asynchronous model computation: its request, status and result."""

    __tablename__ = "model_runs"
    __table_args__ = (
        CheckConstraint(f"status in ({_in(MODEL_RUN_STATUSES)})", name="status"),
        CheckConstraint(f"kind in ({_in(MODEL_RUN_KINDS)})", name="kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30), default="forecast")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
