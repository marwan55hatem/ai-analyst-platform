"""Dataset request and response schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ColumnMapping(BaseModel):
    """Original header and the normalised column name used in SQL."""

    original: str
    normalized: str


class DatasetVersionOut(BaseModel):
    """One uploaded version (storage paths are never exposed)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    original_filename: str
    row_count: int | None
    col_count: int | None
    size_bytes: int
    status: str
    error: str | None
    sheet: str | None
    encoding: str | None
    delimiter: str | None
    column_map: list[ColumnMapping] | None
    warnings: list[str] | None
    profile_status: str
    profile_error: str | None
    quality_score: int | None
    source_version_id: uuid.UUID | None
    created_at: datetime


class DatasetOut(BaseModel):
    """Dataset summary used in lists."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    status: str
    current_version_id: uuid.UUID | None
    created_by: uuid.UUID | None
    created_at: datetime
    latest_version: DatasetVersionOut | None = None


class DatasetDetail(DatasetOut):
    """Dataset with every version, newest first."""

    versions: list[DatasetVersionOut] = Field(default_factory=list)


class SheetsOut(BaseModel):
    """Sheet names of an Excel dataset version."""

    sheets: list[str]


class PreviewColumn(BaseModel):
    """A column in a preview."""

    name: str
    original: str | None
    type: str


class PreviewOut(BaseModel):
    """First rows of a dataset version."""

    version_id: uuid.UUID
    columns: list[PreviewColumn]
    rows: list[list[Any]]
    row_count: int
    total_rows: int | None


class ColumnChange(BaseModel):
    """A column whose inferred type or role differs from the previous version."""

    name: str
    previous_type: str
    new_type: str
    previous_role: str
    new_role: str


class CompatibilityOut(BaseModel):
    """How a new version compares with the previous one (never blocks on added columns)."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    version: int
    previous_version: int | None
    status: str  # pending | initial | compatible | warnings | blocked
    added: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    changed: list[ColumnChange] = Field(default_factory=list)
    missing_roles: list[str] = Field(default_factory=list)
    shorter_period: bool = False
    previous_rows: int | None = None
    new_rows: int | None = None
    previous_first_date: str | None = None
    new_first_date: str | None = None
    blocking: bool = False
    blocking_reason: str | None = None


class VersionSummary(BaseModel):
    """One row of a dataset's version history."""

    id: uuid.UUID
    version: int
    created_at: datetime
    source: str  # upload | cleaning
    original_filename: str
    row_count: int | None
    quality_score: int | None
    status: str
    profile_status: str
    auto_analyze: bool
    is_current: bool
    analysis_status: str | None
    alerts_raised: int
