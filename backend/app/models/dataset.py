"""Dataset and dataset version models."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, created_at_column


class VersionStatus(StrEnum):
    """Lifecycle of an uploaded file."""

    uploaded = "uploaded"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class ProfileStatus(StrEnum):
    """Lifecycle of a version's profile, separate from the file's own status."""

    pending = "pending"
    running = "running"
    ready = "ready"
    failed = "failed"


class Dataset(TenantMixin, Base):
    """A named dataset; its data lives in versions."""

    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "dataset_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_datasets_current_version_id_dataset_versions",
        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), default=VersionStatus.uploaded.value)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DatasetVersion(TenantMixin, Base):
    """One uploaded file and its Parquet conversion."""

    __tablename__ = "dataset_versions"
    __table_args__ = (
        CheckConstraint("status in ('uploaded', 'processing', 'ready', 'failed')", name="status"),
        CheckConstraint(
            "profile_status in ('pending', 'running', 'ready', 'failed')", name="profile_status"
        ),
        UniqueConstraint("dataset_id", "version", name="uq_dataset_versions_dataset_id_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    original_filename: Mapped[str] = mapped_column(String(255))
    original_path: Mapped[str] = mapped_column(String(500))
    parquet_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    col_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(20), default=VersionStatus.uploaded.value)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sheet: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encoding: Mapped[str | None] = mapped_column(String(50), nullable=True)
    delimiter: Mapped[str | None] = mapped_column(String(5), nullable=True)
    column_map: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    profile_status: Mapped[str] = mapped_column(
        String(20), default=ProfileStatus.pending.value, server_default=ProfileStatus.pending.value
    )
    profile_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    dataset_profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    cleaning_plan: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
