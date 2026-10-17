"""Quality score (0-100): completeness 30, validity 25, uniqueness, consistency, sufficiency 15.

Each dimension starts at its maximum and loses points for measured problems; the lost points are
shared out among plain-language issues so the report can say what costs the most.
"""

import math
from dataclasses import dataclass, field

from app.services.profiling.results import (
    ColumnResult,
    Issue,
    QualityReport,
    confidence_for,
    fix_id,
)

MAX_POINTS = {
    "completeness": 30.0,
    "validity": 25.0,
    "uniqueness": 15.0,
    "consistency": 15.0,
    "sufficiency": 15.0,
}
MIN_ROWS = 30
FULL_ROWS = 1000
FULL_HISTORY_DAYS = 365
MAX_ISSUES = 5
NULL_PENALTY_FACTOR = 6.0
GAPPY_COLUMN_PCT = 5.0  # a column with at least this % empty counts as "gappy"
GAPPY_WEIGHT = 0.25
CAST_WEIGHT = 1.0
TEXT_DEFECT_FACTOR = 15
DUPLICATE_PENALTY_FACTOR = 10.0
TEXT_TYPES = {"text", "categorical", "identifier"}


@dataclass
class _Component:
    """A piece of an issue and how much it weighs inside its dimension."""

    weight: float
    issue: Issue
    dimension: str


@dataclass
class _Dimension:
    score: float
    components: list[_Component] = field(default_factory=list)


def _label(column: ColumnResult) -> str:
    return column.original_name or column.name


def _new_issue(
    code: str,
    title: str,
    detail: str,
    columns: list[ColumnResult],
    dimension: str,
    rows: int,
    suggestion: str,
    fix: str | None,
) -> Issue:
    return Issue(
        code=code,
        title=title,
        detail=detail,
        columns=[c.name for c in columns],
        points_lost=0.0,
        dimension=dimension,
        sample_size=rows,
        confidence=confidence_for(rows),
        suggested_fix=suggestion,
        fix_id=fix,
    )


def _completeness(columns: list[ColumnResult], rows: int) -> _Dimension:
    avg_null = sum(c.stats["null_pct"] for c in columns) / len(columns) / 100 if columns else 0.0
    gappy = sum(1 for c in columns if c.stats["null_pct"] >= GAPPY_COLUMN_PCT)
    loss = NULL_PENALTY_FACTOR * avg_null + GAPPY_WEIGHT * gappy / max(len(columns), 1)
    score = MAX_POINTS["completeness"] * max(0.0, 1 - loss)
    dim = _Dimension(score)
    for c in columns:
        pct = c.stats["null_pct"]
        if pct < 1.0:
            continue
        missing = c.stats["null_count"]
        issue = _new_issue(
            "missing_values",
            f"{pct:.0f}% of '{_label(c)}' is empty",
            f"{missing:,} of {rows:,} rows have no value in '{_label(c)}'.",
            [c],
            "completeness",
            rows,
            "Fill the gaps with a typical value (median for numbers, most common value for "
            "categories), or leave them if blank is meaningful.",
            fix_id("nulls", c.name),
        )
        dim.components.append(_Component(pct, issue, "completeness"))
    return dim


def _validity(columns: list[ColumnResult], rows: int) -> _Dimension:
    n = max(len(columns), 1)
    cast_cols = [c for c in columns if c.stats.get("needs_cast")]
    mixed_cols = [c for c in columns if c.stats.get("mixed_types")]
    invalid_share = [
        c.stats["invalid_count"] / c.stats["non_null_count"]
        for c in columns
        if c.stats.get("invalid_count") and c.stats.get("non_null_count")
    ]
    outlier_share = [
        c.stats["outlier_count"] / c.stats["non_null_count"]
        for c in columns
        if c.stats.get("outlier_count") and c.stats.get("non_null_count")
    ]
    penalty = (
        CAST_WEIGHT * len(cast_cols) / n
        + min(0.5, 4.0 * sum(invalid_share) / n)
        + 0.3 * len(mixed_cols) / n
        + 0.1 * min(1.0, 5.0 * sum(outlier_share) / n)
    )
    dim = _Dimension(MAX_POINTS["validity"] * max(0.0, 1 - penalty))
    for c in cast_cols:
        kind = "dates" if c.inferred_type == "datetime" else "numbers"
        invalid = c.stats.get("invalid_count", 0)
        extra = f" {invalid:,} values could not be read and would become empty." if invalid else ""
        fix_kind = "parse_dates" if c.inferred_type == "datetime" else "cast"
        issue = _new_issue(
            "stored_as_text",
            f"'{_label(c)}' holds {kind} saved as text",
            f"Currency signs, separators or mixed formats keep '{_label(c)}' from being a real "
            f"{c.inferred_type} column.{extra}",
            [c],
            "validity",
            rows,
            f"Convert '{_label(c)}' to a proper {kind[:-1]} column.",
            fix_id(fix_kind, c.name),
        )
        dim.components.append(
            _Component(CAST_WEIGHT / n + (invalid / max(rows, 1)) * 4 / n, issue, "validity")
        )
    for c in mixed_cols:
        issue = _new_issue(
            "mixed_types",
            f"'{_label(c)}' mixes numbers and text",
            f"Only {c.stats['numeric_share'] * 100:.0f}% of '{_label(c)}' looks like a number.",
            [c],
            "validity",
            rows,
            "Check the text entries in this column before using it for calculations.",
            None,
        )
        dim.components.append(_Component(0.3 / n, issue, "validity"))
    for c in columns:
        if c.stats.get("needs_cast") or not c.stats.get("outlier_count"):
            continue
        share = c.stats["outlier_count"] / max(c.stats["non_null_count"], 1)
        issue = _new_issue(
            "outliers",
            f"'{_label(c)}' has {c.stats['outlier_count']:,} unusually large or small values",
            f"{share * 100:.1f}% of values fall outside the normal range "
            f"({c.stats['outlier_bounds']['lower']} to {c.stats['outlier_bounds']['upper']}).",
            [c],
            "validity",
            rows,
            "Review them; they will be flagged, never deleted.",
            fix_id("flag_outliers", c.name),
        )
        dim.components.append(_Component(0.5 * share / n, issue, "validity"))
    return dim


def _uniqueness(duplicate_rows: int, rows: int) -> _Dimension:
    share = duplicate_rows / rows if rows else 0.0
    dim = _Dimension(MAX_POINTS["uniqueness"] * max(0.0, 1 - DUPLICATE_PENALTY_FACTOR * share))
    if duplicate_rows:
        issue = _new_issue(
            "duplicate_rows",
            f"{duplicate_rows:,} rows ({share * 100:.1f}%) are exact duplicates",
            "Identical rows are counted twice in totals and averages.",
            [],
            "uniqueness",
            rows,
            "Remove the repeated rows, keeping the first of each.",
            fix_id("dedupe"),
        )
        dim.components.append(_Component(share, issue, "uniqueness"))
    return dim


def _consistency(columns: list[ColumnResult], rows: int) -> _Dimension:
    text_cols = [c for c in columns if c.inferred_type in TEXT_TYPES]
    cat_cols = [c for c in columns if c.inferred_type == "categorical"]
    date_cols = [c for c in columns if c.inferred_type == "datetime"]
    ws_cells = sum(c.stats.get("whitespace_count", 0) for c in text_cols)
    ws_share = ws_cells / (rows * len(text_cols)) if rows and text_cols else 0.0
    case_cells = sum(c.stats.get("case_variant_cells", 0) for c in cat_cols)
    case_share = case_cells / (rows * len(cat_cols)) if rows and cat_cols else 0.0
    multi = [c for c in date_cols if c.stats.get("format_count", 1) > 1]
    ambiguous = [c for c in date_cols if c.stats.get("ambiguous")]
    n_dates = max(len(date_cols), 1)
    penalty = (
        0.25 * min(1.0, TEXT_DEFECT_FACTOR * ws_share)
        + 0.3 * min(1.0, TEXT_DEFECT_FACTOR * case_share)
        + 0.25 * len(multi) / n_dates
        + 0.2 * len(ambiguous) / n_dates
    )
    dim = _Dimension(MAX_POINTS["consistency"] * max(0.0, 1 - penalty))
    for c in text_cols:
        count = c.stats.get("whitespace_count", 0)
        if not count:
            continue
        issue = _new_issue(
            "extra_whitespace",
            f"'{_label(c)}' has stray spaces around {count:,} values",
            "Values like ' North' and 'North' are treated as different categories.",
            [c],
            "consistency",
            rows,
            "Trim the spaces.",
            fix_id("trim", c.name),
        )
        dim.components.append(
            _Component(0.25 * min(1.0, 8 * count / rows) if rows else 0.0, issue, "consistency")
        )
    for c in cat_cols:
        groups = c.stats.get("case_variant_groups", 0)
        if not groups:
            continue
        cells = c.stats["case_variant_cells"]
        issue = _new_issue(
            "inconsistent_case",
            f"'{_label(c)}' spells {groups} categories in different ways",
            f"{cells:,} values differ only by capital letters (for example 'north' and 'North').",
            [c],
            "consistency",
            rows,
            "Use one spelling per category (the most common one).",
            fix_id("case", c.name),
        )
        dim.components.append(
            _Component(0.3 * min(1.0, 8 * cells / rows) if rows else 0.0, issue, "consistency")
        )
    for c in multi:
        issue = _new_issue(
            "mixed_date_formats",
            f"'{_label(c)}' uses {c.stats['format_count']} different date formats",
            "Dates written in different styles are hard to sort and group.",
            [c],
            "consistency",
            rows,
            "Convert all dates to one standard format.",
            fix_id("parse_dates", c.name),
        )
        dim.components.append(_Component(0.25 / n_dates, issue, "consistency"))
    for c in ambiguous:
        issue = _new_issue(
            "ambiguous_dates",
            f"'{_label(c)}' has dates that could be day-first or month-first",
            "A date like 03/04/2024 could mean 3 April or 4 March; the data does not say which.",
            [c],
            "consistency",
            rows,
            "Confirm the date order before converting (this fix is high risk).",
            fix_id("parse_dates", c.name),
        )
        dim.components.append(_Component(0.2 / n_dates, issue, "consistency"))
    return dim


def _history_days(columns: list[ColumnResult]) -> int | None:
    spans = [
        int(c.stats["span_days"])
        for c in columns
        if c.inferred_type == "datetime" and c.stats.get("span_days") is not None
    ]
    return max(spans) if spans else None


def _sufficiency(columns: list[ColumnResult], rows: int) -> _Dimension:
    row_score = (
        min(1.0, max(0.0, math.log10(rows / MIN_ROWS) / math.log10(FULL_ROWS / MIN_ROWS)))
        if rows > 0
        else 0.0
    )
    history = _history_days(columns)
    history_score = 0.5 if history is None else min(1.0, history / FULL_HISTORY_DAYS)
    dim = _Dimension(MAX_POINTS["sufficiency"] * (0.6 * row_score + 0.4 * history_score))
    if row_score < 1.0:
        issue = _new_issue(
            "few_rows",
            f"Only {rows:,} rows",
            f"Fewer than {FULL_ROWS:,} rows limits how reliable averages, trends and models are.",
            [],
            "sufficiency",
            rows,
            "Add more data if you can; insights from this dataset are marked with low confidence.",
            None,
        )
        dim.components.append(_Component(0.6 * (1 - row_score), issue, "sufficiency"))
    if history is not None and history_score < 1.0:
        issue = _new_issue(
            "short_history",
            f"Only {history:,} days of history",
            "Seasonal patterns and year-over-year comparisons need at least a year of data.",
            [],
            "sufficiency",
            rows,
            "Upload a longer period if available.",
            None,
        )
        dim.components.append(_Component(0.4 * (1 - history_score), issue, "sufficiency"))
    return dim


def score_quality(columns: list[ColumnResult], rows: int, duplicate_rows: int) -> QualityReport:
    """Compute the 0-100 score, its breakdown and the five costliest issues."""
    dims = {
        "completeness": _completeness(columns, rows),
        "validity": _validity(columns, rows),
        "uniqueness": _uniqueness(duplicate_rows, rows),
        "consistency": _consistency(columns, rows),
        "sufficiency": _sufficiency(columns, rows),
    }
    issues: list[Issue] = []
    for name, dim in dims.items():
        loss = MAX_POINTS[name] - dim.score
        total_weight = sum(c.weight for c in dim.components)
        if loss <= 0 or total_weight <= 0:
            continue
        for comp in dim.components:
            comp.issue.points_lost = loss * comp.weight / total_weight
            issues.append(comp.issue)
    issues = sorted((i for i in issues if i.points_lost >= 0.1), key=lambda i: -i.points_lost)
    breakdown = {name: dim.score for name, dim in dims.items()}
    warnings = []
    if rows < MIN_ROWS:
        warnings.append(f"Not enough data: {rows} rows is too few for a reliable assessment.")
    return QualityReport(
        score=round(sum(breakdown.values())),
        breakdown=breakdown,
        issues=issues[:MAX_ISSUES],
        sample_size=rows,
        confidence=confidence_for(rows),
        warnings=warnings,
    )
