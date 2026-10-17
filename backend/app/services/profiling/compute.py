"""Compute a complete profile of one Parquet file (pure: no database access)."""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.llm.base import LLMClient
from app.services.profiling.cleaning_plan import build_plan
from app.services.profiling.column_stats import profile_column
from app.services.profiling.engine import SOURCE_VIEW, open_parquet, scalar, schema_of
from app.services.profiling.parsing import quote_ident
from app.services.profiling.quality import score_quality
from app.services.profiling.results import ColumnResult, Fix, QualityReport
from app.services.profiling.role_refine import refine_roles
from app.services.profiling.roles import assign_roles, role_summary

HIGH_CARDINALITY_MIN_DISTINCT = 50
HIGH_CARDINALITY_RATIO = 0.5


@dataclass
class ProfileResult:
    """Everything the profile stage stores for one version."""

    columns: list[ColumnResult]
    dataset: dict[str, Any]
    quality: QualityReport
    plan: list[Fix]
    llm_roles_applied: int = 0


def json_safe(value: Any) -> Any:
    """Replace NaN/inf (invalid in JSON) with None, recursively."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    return value


def _duplicate_rows(con: Any, names: list[str], rows: int) -> int:
    """Count rows that repeat an earlier row exactly (64-bit hash of the whole row)."""
    if not names or rows == 0:
        return 0
    cols = ", ".join(quote_ident(n) for n in names)
    distinct = int(scalar(con, f"SELECT count(DISTINCT hash({cols})) FROM {SOURCE_VIEW}"))
    return rows - distinct


def _date_summary(columns: list[ColumnResult]) -> dict[str, Any] | None:
    dates = [c for c in columns if c.inferred_type == "datetime"]
    if not dates:
        return None
    primary = max(
        dates, key=lambda c: (c.semantic_role == "date", c.role_confidence, c.stats["span_days"])
    )
    keys = (
        "min",
        "max",
        "span_days",
        "granularity",
        "gap_count",
        "largest_gap_days",
        "missing_periods",
        "distinct_dates",
    )
    return {"column": primary.name, **{k: primary.stats.get(k) for k in keys}}


def _dataset_summary(columns: list[ColumnResult], rows: int, duplicate_rows: int) -> dict[str, Any]:
    constant = [
        c.name for c in columns if c.stats["distinct_count"] <= 1 and c.stats["non_null_count"] > 0
    ]
    high_cardinality = [
        c.name
        for c in columns
        if c.inferred_type in {"categorical", "text"}
        and c.stats["distinct_count"] > HIGH_CARDINALITY_MIN_DISTINCT
        and c.stats["distinct_ratio"] >= HIGH_CARDINALITY_RATIO
    ]
    return {
        "row_count": rows,
        "column_count": len(columns),
        "duplicate_rows": duplicate_rows,
        "duplicate_pct": round(100.0 * duplicate_rows / rows, 2) if rows else 0.0,
        "constant_columns": constant,
        "high_cardinality_columns": high_cardinality,
        "date_range": _date_summary(columns),
        "roles": role_summary(columns),
        "pii_columns": [c.name for c in columns if c.pii_flag],
    }


def compute_profile(
    parquet: Path,
    column_map: list[dict[str, str]] | None = None,
    *,
    user_roles: dict[str, str] | None = None,
    llm: LLMClient | None = None,
) -> ProfileResult:
    """Profile every column, detect roles, score quality and build the cleaning plan."""
    originals = {m["normalized"]: m["original"] for m in (column_map or [])}
    with open_parquet(parquet) as con:
        schema = schema_of(con)
        rows = int(scalar(con, f"SELECT count(*) FROM {SOURCE_VIEW}"))
        columns = [profile_column(con, n, originals.get(n, n), t, rows) for n, t in schema]
        duplicates = _duplicate_rows(con, [n for n, _ in schema], rows)
        assign_roles(columns, user_roles)
        applied = refine_roles(con, columns, dict(schema), llm)
    for column in columns:
        column.stats = json_safe(column.stats)
    quality = score_quality(columns, rows, duplicates)
    plan = build_plan(columns, duplicates)
    return ProfileResult(
        columns=columns,
        dataset=json_safe(_dataset_summary(columns, rows, duplicates)),
        quality=quality,
        plan=plan,
        llm_roles_applied=applied,
    )
