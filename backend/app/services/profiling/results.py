"""Typed results of the profiling stage."""

from dataclasses import dataclass, field
from typing import Any, Literal

Confidence = Literal["high", "medium", "low"]
Risk = Literal["low", "medium", "high"]

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
NUMERIC_ROLES = frozenset({"revenue", "quantity", "price", "cost", "discount"})
NUMERIC_TYPES = frozenset({"integer", "float"})


def fix_id(kind: str, column: str | None = None) -> str:
    """Build the stable id of a cleaning fix (`trim:city`, `dedupe`)."""
    return kind if column is None else f"{kind}:{column}"


def confidence_for(sample_size: int) -> Confidence:
    """Map a sample size to a confidence level (golden rule 3)."""
    if sample_size >= 1000:
        return "high"
    if sample_size >= 100:
        return "medium"
    return "low"


@dataclass
class ColumnResult:
    """Everything the profiler learned about one column."""

    name: str
    original_name: str
    inferred_type: str
    stats: dict[str, Any]
    pii_kind: str | None = None
    semantic_role: str = "other"
    role_confidence: float = 0.0
    role_source: str = "rule"

    @property
    def pii_flag(self) -> bool:
        """True when the column holds personal data."""
        return self.pii_kind is not None


@dataclass
class Issue:
    """A data-quality problem in plain language with a suggested fix."""

    code: str
    title: str
    detail: str
    columns: list[str]
    points_lost: float
    dimension: str
    sample_size: int
    confidence: Confidence
    suggested_fix: str
    fix_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict."""
        return {
            "code": self.code,
            "title": self.title,
            "detail": self.detail,
            "columns": self.columns,
            "points_lost": round(self.points_lost, 1),
            "dimension": self.dimension,
            "sample_size": self.sample_size,
            "confidence": self.confidence,
            "suggested_fix": self.suggested_fix,
            "fix_id": self.fix_id,
        }


@dataclass
class QualityReport:
    """Quality score (0-100) with its breakdown and the top issues."""

    score: int
    breakdown: dict[str, float]
    issues: list[Issue]
    sample_size: int
    confidence: Confidence
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict (stored on the version)."""
        return {
            "score": self.score,
            "breakdown": {k: round(v, 1) for k, v in self.breakdown.items()},
            "max": {
                "completeness": 30,
                "validity": 25,
                "uniqueness": 15,
                "consistency": 15,
                "sufficiency": 15,
            },
            "issues": [i.as_dict() for i in self.issues],
            "sample_size": self.sample_size,
            "confidence": self.confidence,
            "method": "weighted dimensions: completeness 30, validity 25, uniqueness 15, "
            "consistency 15, sufficiency 15",
            "warnings": self.warnings,
        }


@dataclass
class Fix:
    """One proposed cleaning fix (never applied automatically)."""

    id: str
    type: str
    description: str
    columns: list[str]
    rows_affected: int
    cells_affected: int
    risk: Risk
    recommended: bool
    strategies: list[str] | None = None
    default_strategy: str | None = None
    strategy_risk: dict[str, Risk] | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict (stored on the version)."""
        return {
            "id": self.id,
            "type": self.type,
            "description": self.description,
            "columns": self.columns,
            "rows_affected": self.rows_affected,
            "cells_affected": self.cells_affected,
            "risk": self.risk,
            "recommended": self.recommended,
            "strategies": self.strategies,
            "default_strategy": self.default_strategy,
            "strategy_risk": self.strategy_risk,
            "params": self.params,
        }
