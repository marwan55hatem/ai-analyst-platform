"""Column profiles and cleaning logs."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin

COLUMN_TYPES = (
    "integer",
    "float",
    "boolean",
    "datetime",
    "categorical",
    "text",
    "identifier",
)
ROLES = (
    "date",
    "revenue",
    "quantity",
    "price",
    "cost",
    "discount",
    "customer_id",
    "order_id",
    "product",
    "category",
    "region",
    "channel",
    "status",
    "email",
    "phone",
    "churn_flag",
    "other",
)
ROLE_SOURCES = ("rule", "llm", "user")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class ColumnProfile(TenantMixin, Base):
    """Statistics, inferred type and business role of one column of one dataset version."""

    __tablename__ = "column_profiles"
    __table_args__ = (
        UniqueConstraint("dataset_version_id", "name", name="uq_column_profiles_version_name"),
        CheckConstraint(f"inferred_type in ({_in(COLUMN_TYPES)})", name="inferred_type"),
        CheckConstraint(f"semantic_role in ({_in(ROLES)})", name="semantic_role"),
        CheckConstraint(f"role_source in ({_in(ROLE_SOURCES)})", name="role_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    original_name: Mapped[str] = mapped_column(String(500))
    inferred_type: Mapped[str] = mapped_column(String(20))
    semantic_role: Mapped[str] = mapped_column(String(20), default="other")
    role_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    role_source: Mapped[str] = mapped_column(String(10), default="rule")
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    pii_flag: Mapped[bool] = mapped_column(Boolean, default=False)


class CleaningLog(TenantMixin, Base):
    """One applied cleaning fix, attached to the version it created."""

    __tablename__ = "cleaning_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), index=True
    )
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL"), nullable=True
    )
    fix_id: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    rows_affected: Mapped[int] = mapped_column(BigInteger, default=0)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
