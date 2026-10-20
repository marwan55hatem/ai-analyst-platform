"""Helpers shared by the analysis modules."""

import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.ml.base import AnalysisContext, Confidence, Insight, ModuleResult
from app.ml.engine import num
from app.ml.impact import impact_score
from app.ml.quality import cap_confidence
from app.services.profiling.results import confidence_for

MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
EVIDENCE_ROWS = 10
# Descriptive insights (headline KPIs, concentration) score the effect of a 10% swing.
SENSITIVITY = 0.10
# (unit, periods in a year) by the dataset's detected date granularity
_PERIOD_BY_GRANULARITY = {"quarterly": ("quarter", 4), "yearly": ("year", 1)}


def jsonable(value: Any) -> Any:
    """Convert Decimal, dates and NaN to JSON-safe values, recursively."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    return value


def evidence_query(purpose: str, sql: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return one evidence entry: what the query shows, the exact SQL and a small result table."""
    return {
        "purpose": purpose,
        "sql": sql,
        "columns": list(rows[0].keys()) if rows else [],
        "rows": jsonable([list(r.values()) for r in rows[:EVIDENCE_ROWS]]),
        "row_count": len(rows),
    }


def revenue_expr(ctx: AnalysisContext) -> str:
    """Return the SQL for revenue (the revenue column, or quantity x price)."""
    expr = ctx.roles.revenue_expr()
    if expr is None:
        raise ValueError("no revenue available")
    return expr


def revenue_note(ctx: AnalysisContext) -> str:
    """Return a sentence explaining derived revenue, or an empty string."""
    if ctx.roles.derived_revenue:
        return "Revenue was derived as quantity x price because the file has no revenue column."
    return ""


def base_revenue(ctx: AnalysisContext) -> float:
    """Return total positive sales; it is the yardstick for every impact score."""
    if "base_revenue" not in ctx.cache:
        rev = revenue_expr(ctx)
        ctx.cache["base_revenue"] = num(
            ctx.db.scalar(f"SELECT sum({rev}) FROM data WHERE {rev} > 0")
        )
    return float(ctx.cache["base_revenue"])


def refund_rows(ctx: AnalysisContext) -> int:
    """Count rows with negative revenue (returns/refunds), cached per run."""
    if "refund_rows" not in ctx.cache:
        rev = revenue_expr(ctx)
        ctx.cache["refund_rows"] = int(
            ctx.db.scalar(f"SELECT count(*) FROM data WHERE {rev} < 0") or 0
        )
    return int(ctx.cache["refund_rows"])


def excluded_rows(ctx: AnalysisContext, columns: list[str]) -> int:
    """Count rows a module drops because one of its key columns is empty."""
    if not columns:
        return 0
    cond = " OR ".join(f'"{c}" IS NULL' for c in columns)
    return int(ctx.db.scalar(f"SELECT count(*) FROM data WHERE {cond}") or 0)


def revenue_key_columns(ctx: AnalysisContext) -> list[str]:
    """Return the columns revenue depends on."""
    return ctx.roles.revenue_columns()


def period_unit(granularity: str | None) -> tuple[str, int]:
    """Return (reporting unit, periods per year) for a date granularity."""
    return _PERIOD_BY_GRANULARITY.get(granularity or "", ("month", 12))


def is_complete_period(last_day: date, unit: str) -> bool:
    """Return whether data ending on `last_day` covers its whole month/quarter/year."""
    nxt = last_day + timedelta(days=1)
    if unit == "year":
        return nxt.month == 1 and nxt.day == 1
    if unit == "quarter":
        return nxt.day == 1 and nxt.month in (1, 4, 7, 10)
    return nxt.day == 1


def shift_period(start: date, unit: str, steps: int) -> date:
    """Return the start of the period `steps` periods after `start` (negative goes back)."""
    months = {"month": 1, "quarter": 3, "year": 12}[unit] * steps
    total = start.year * 12 + (start.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def series_confidence(periods: int, rows: int) -> Confidence:
    """Confidence from the number of periods observed and the rows behind them."""
    by_rows = confidence_for(rows)
    by_periods: Confidence = "low" if periods < 6 else "medium" if periods < 12 else "high"
    return cap_confidence(by_rows, by_periods)  # type: ignore[return-value]


def build_insight(
    ctx: AnalysisContext,
    *,
    module: str,
    type: str,
    title: str,
    summary: str,
    value: Any,
    sample_size: int,
    method: str,
    key_columns: list[str],
    money_effect: float,
    payload: dict[str, Any],
    chart_spec: dict[str, Any],
    evidence: list[dict[str, Any]],
    excluded: int = 0,
    confidence: Confidence | None = None,
    warnings: tuple[str, ...] = (),
    what: str = "figures",
) -> Insight:
    """Assemble an insight: apply data-quality caveats and the confidence cap, score its impact."""
    caveats = ctx.quality.caveats(key_columns, excluded, what)
    level = cap_confidence(confidence or confidence_for(sample_size), caveats.cap)
    all_warnings = (*warnings, *caveats.warnings)
    text = summary if not all_warnings else f"{summary} Note: {' '.join(all_warnings)}"
    result = ModuleResult(
        value=jsonable(value),
        confidence=level,  # type: ignore[arg-type]
        sample_size=sample_size,
        method=method,
        warnings=all_warnings,
    )
    return Insight(
        module=module,
        type=type,
        title=title,
        summary=text,
        result=result,
        impact_score=impact_score(money_effect, base_revenue(ctx), level),
        money_effect=round(float(money_effect), 2),
        payload=jsonable({**payload, "excluded_rows": excluded}),
        chart_spec=jsonable(chart_spec),
        evidence={"queries": evidence},
    )


def as_date(value: Any) -> date:
    """Normalize a DuckDB date/timestamp value to a date."""
    return value.date() if isinstance(value, datetime) else value


def pct_change(new: float, old: float) -> float | None:
    """Return the percentage change from old to new, or None when old is zero."""
    return None if old == 0 else 100.0 * (new - old) / abs(old)
