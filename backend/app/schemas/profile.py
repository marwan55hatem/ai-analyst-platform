"""Profile, quality and cleaning-plan schemas."""

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.profile import ROLES


class ColumnProfileOut(BaseModel):
    """Statistics, type and business role of one column."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    original_name: str
    inferred_type: str
    semantic_role: str
    role_confidence: float
    role_source: str
    pii_flag: bool
    stats: dict[str, Any]


class ProfileOut(BaseModel):
    """Column-level and dataset-level profile of a version."""

    version_id: uuid.UUID
    version: int
    profile_status: str
    row_count: int | None
    column_count: int | None
    dataset: dict[str, Any]
    columns: list[ColumnProfileOut]


class QualityOut(BaseModel):
    """Quality score with its breakdown and the top issues."""

    version_id: uuid.UUID
    version: int
    score: int
    breakdown: dict[str, float]
    max: dict[str, int]
    issues: list[dict[str, Any]]
    sample_size: int
    confidence: str
    method: str
    warnings: list[str]


class FixOut(BaseModel):
    """One proposed cleaning fix."""

    id: str
    type: str
    description: str
    columns: list[str]
    rows_affected: int
    cells_affected: int
    risk: str
    recommended: bool
    strategies: list[str] | None
    default_strategy: str | None
    strategy_risk: dict[str, str] | None
    params: dict[str, Any]


class CleaningPlanOut(BaseModel):
    """Proposed fixes for a version; none is applied automatically."""

    version_id: uuid.UUID
    version: int
    quality_score: int | None
    fixes: list[FixOut]


class FixChoice(BaseModel):
    """One fix the user wants applied, with an optional strategy."""

    id: str = Field(min_length=1, max_length=300)
    strategy: str | None = Field(default=None, max_length=50)


class ApplyRequest(BaseModel):
    """The fixes to apply; only the ids listed here are ever applied."""

    fixes: list[FixChoice] = Field(max_length=200)


class RoleUpdate(BaseModel):
    """A user's choice of business role for a column."""

    role: str

    @field_validator("role")
    @classmethod
    def known_role(cls, value: str) -> str:
        """Reject roles outside the supported list."""
        if value not in ROLES:
            raise ValueError(f"role must be one of: {', '.join(ROLES)}")
        return value
