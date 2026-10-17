"""Build the (never auto-applied) cleaning plan from a profile.

Defaults, as agreed: only low-risk fixes (trim, case standardization, safe type casts) are
marked `recommended`; dedupe is medium risk; parsing ambiguous dd/mm dates is high risk; null
handling defaults to the strategy `keep` (no change); outlier flagging only adds a column.
Nothing medium or high is ever applied unless the user lists its id in the apply request.
"""

from typing import Any

from app.services.profiling.results import NUMERIC_TYPES, ColumnResult, Fix, Risk, fix_id

FIX_ORDER = ("trim", "cast", "parse_dates", "case", "dedupe", "nulls", "flag_outliers")
KEEP = "keep"
DROP_ROWS_HIGH_RISK_PCT = 5.0
TEXT_TYPES = {"text", "categorical", "identifier"}


def _label(column: ColumnResult) -> str:
    return column.original_name or column.name


def _trim_fixes(columns: list[ColumnResult]) -> list[Fix]:
    fixes = []
    for c in columns:
        count = c.stats.get("whitespace_count", 0)
        if c.inferred_type in TEXT_TYPES and count:
            fixes.append(
                Fix(
                    id=fix_id("trim", c.name),
                    type="trim",
                    description=f"Remove spaces at the start and end of {count:,} values in "
                    f"'{_label(c)}'.",
                    columns=[c.name],
                    rows_affected=count,
                    cells_affected=count,
                    risk="low",
                    recommended=True,
                )
            )
    return fixes


def _cast_fixes(columns: list[ColumnResult]) -> list[Fix]:
    fixes = []
    for c in columns:
        stats = c.stats
        is_numeric = c.inferred_type in NUMERIC_TYPES and stats.get("needs_cast")
        is_bool = c.inferred_type == "boolean" and stats.get("stored_as") == "text"
        if not (is_numeric or is_bool):
            continue
        target = "boolean" if is_bool else c.inferred_type
        invalid = int(stats.get("invalid_count", 0))
        risk: Risk = "low" if invalid == 0 else "medium"
        note = f" {invalid:,} values cannot be read and would become empty." if invalid else ""
        what = (
            "yes/no values"
            if is_bool
            else "numbers (currency signs, spaces and separators removed)"
        )
        fixes.append(
            Fix(
                id=fix_id("cast", c.name),
                type="cast",
                description=f"Convert '{_label(c)}' from text to {what}.{note}",
                columns=[c.name],
                rows_affected=int(stats["non_null_count"]),
                cells_affected=int(stats["non_null_count"]),
                risk=risk,
                recommended=risk == "low",
                params={"target": target, "invalid_count": invalid},
            )
        )
    return fixes


def _date_fixes(columns: list[ColumnResult]) -> list[Fix]:
    fixes = []
    for c in columns:
        stats = c.stats
        if c.inferred_type != "datetime" or stats.get("stored_as") != "text":
            continue
        invalid = int(stats.get("invalid_count", 0))
        ambiguous = bool(stats.get("ambiguous"))
        risk: Risk = "high" if ambiguous else ("medium" if invalid else "low")
        dayfirst = bool(stats.get("dayfirst", True))
        note = ""
        if ambiguous:
            note = (
                " Some dates fit day/month and month/day; day-first is assumed unless you "
                "choose otherwise."
            )
        elif invalid:
            note = f" {invalid:,} values cannot be read and would become empty."
        fixes.append(
            Fix(
                id=fix_id("parse_dates", c.name),
                type="parse_dates",
                description=f"Convert '{_label(c)}' to real dates ({stats.get('format_count', 1)} "
                f"format(s) found).{note}",
                columns=[c.name],
                rows_affected=int(stats.get("non_null_count", 0)),
                cells_affected=int(stats.get("non_null_count", 0)),
                risk=risk,
                recommended=risk == "low",
                strategies=["dayfirst", "monthfirst"] if ambiguous else None,
                default_strategy=("dayfirst" if dayfirst else "monthfirst") if ambiguous else None,
                params={
                    "dayfirst": dayfirst,
                    "target": "timestamp" if stats.get("has_time") else "date",
                    "invalid_count": invalid,
                },
            )
        )
    return fixes


def _case_fixes(columns: list[ColumnResult]) -> list[Fix]:
    fixes = []
    for c in columns:
        groups = c.stats.get("case_variant_groups", 0)
        if c.inferred_type == "categorical" and groups:
            cells = int(c.stats["case_variant_cells"])
            fixes.append(
                Fix(
                    id=fix_id("case", c.name),
                    type="case",
                    description=f"Use one spelling for {groups} categories in '{_label(c)}' "
                    f"({cells:,} values change).",
                    columns=[c.name],
                    rows_affected=cells,
                    cells_affected=cells,
                    risk="low",
                    recommended=True,
                )
            )
    return fixes


def _dedupe_fix(columns: list[ColumnResult], duplicates: int) -> list[Fix]:
    if duplicates <= 0:
        return []
    return [
        Fix(
            id=fix_id("dedupe"),
            type="dedupe",
            description=f"Remove {duplicates:,} exact duplicate rows (the first copy is kept).",
            columns=[c.name for c in columns],
            rows_affected=duplicates,
            cells_affected=duplicates * len(columns),
            risk="medium",
            recommended=False,
        )
    ]


def strategies_for(column: ColumnResult) -> list[str]:
    """Return the null-handling strategies that make sense for a column."""
    if column.inferred_type in NUMERIC_TYPES:
        return [KEEP, "impute_median", "drop_rows"]
    if column.inferred_type in {"categorical", "boolean"}:
        return [KEEP, "impute_mode", "drop_rows"]
    return [KEEP, "drop_rows"]


def _null_fixes(columns: list[ColumnResult]) -> list[Fix]:
    fixes = []
    for c in columns:
        nulls = int(c.stats["null_count"])
        if nulls == 0 or c.stats["non_null_count"] == 0:
            continue
        strategies = strategies_for(c)
        drop_risk: Risk = "high" if c.stats["null_pct"] > DROP_ROWS_HIGH_RISK_PCT else "medium"
        risks: dict[str, Risk] = {s: "medium" for s in strategies if s.startswith("impute")}
        risks["drop_rows"] = drop_risk
        fixes.append(
            Fix(
                id=fix_id("nulls", c.name),
                type="nulls",
                description=f"{nulls:,} empty values in '{_label(c)}' "
                f"({c.stats['null_pct']:.1f}%). Choose a strategy; the default keeps them.",
                columns=[c.name],
                rows_affected=nulls,
                cells_affected=nulls,
                risk="medium",
                recommended=False,
                strategies=strategies,
                default_strategy=KEEP,
                strategy_risk=risks,
            )
        )
    return fixes


def _outlier_fixes(columns: list[ColumnResult]) -> list[Fix]:
    fixes = []
    for c in columns:
        count = int(c.stats.get("outlier_count", 0))
        if c.inferred_type in NUMERIC_TYPES and count:
            fixes.append(
                Fix(
                    id=fix_id("flag_outliers", c.name),
                    type="flag_outliers",
                    description=f"Add a column '{c.name}_is_outlier' marking {count:,} unusual "
                    f"values in '{_label(c)}'. Nothing is deleted.",
                    columns=[c.name],
                    rows_affected=count,
                    cells_affected=count,
                    risk="low",
                    recommended=False,
                )
            )
    return fixes


def build_plan(columns: list[ColumnResult], duplicate_rows: int) -> list[Fix]:
    """Return every applicable fix, in the order they would be applied."""
    return [
        *_trim_fixes(columns),
        *_cast_fixes(columns),
        *_date_fixes(columns),
        *_case_fixes(columns),
        *_dedupe_fix(columns, duplicate_rows),
        *_null_fixes(columns),
        *_outlier_fixes(columns),
    ]


def plan_to_dicts(fixes: list[Fix]) -> list[dict[str, Any]]:
    """Serialize a plan for storage."""
    return [f.as_dict() for f in fixes]
