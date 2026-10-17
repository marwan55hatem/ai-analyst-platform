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
