"""Schemas of the report endpoints."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReportCreate(BaseModel):
    """What to report on: a version of the dataset (default: the current one) and a language."""

    version: int | None = Field(default=None, ge=1, description="Dataset version number")
    locale: Literal["en", "ar"] = Field(default="en", description="Report language")


class ReportOut(BaseModel):
    """A report and its generation status; poll it until `status` is completed or failed."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    title: str = Field(description="The dataset name when the report was made")
    version: int
    run_id: uuid.UUID | None
    locale: Literal["en", "ar"]
    status: Literal["queued", "running", "completed", "failed"]
    progress: int
    size_bytes: int | None
    error_code: str | None = Field(
        description="no_analysis, too_large, render_failed, timed_out or not_started"
    )
    created_by: uuid.UUID | None
    created_at: datetime
    finished_at: datetime | None


class ReportPage(BaseModel):
    """One page of reports, newest first."""

    items: list[ReportOut]
    total: int
