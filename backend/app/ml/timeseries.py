"""Dashboard time series: headline KPIs for a date range vs the window before it, and a series.

Every number comes from two SQL aggregations on the hardened analysis table; the SQL and its
result are returned as evidence. Revenue is net (sales minus returns/refunds), as in the KPI
module, and the average order value uses gross sales per order like the KPI summary does.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from app.ml.base import AnalysisContext
from app.ml.engine import lit, num
from app.ml.modules.common import as_date, evidence_query, jsonable, series_confidence

Grain = Literal["day", "week", "month"]
METHOD = (
    "SQL aggregation in DuckDB over the selected dates; revenue is net of returns/refunds, the "
    "comparison window is the same number of days just before the range, and periods without "
    "rows count as zero"
)


class SeriesUnavailable(Exception):
    """The data cannot answer the request; `code` and the message go back to the client."""

    def __init__(self, code: str, message: str) -> None:
        """Keep a machine-readable code next to the plain-language message."""
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Window:
    """An inclusive date range."""

    start: date
    end: date

    @property
    def days(self) -> int:
        """Number of calendar days in the window."""
        return (self.end - self.start).days + 1

    def as_dict(self) -> dict[str, str]:
        """Return the window as ISO dates."""
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


def bucket_start(day: date, grain: Grain) -> date:
    """Return the first day of the period that contains `day` (weeks start on Monday)."""
    if grain == "week":
        return day - timedelta(days=day.weekday())
    if grain == "month":
        return day.replace(day=1)
    return day


def next_bucket(start: date, grain: Grain) -> date:
    """Return the first day of the period after the one starting on `start`."""
    if grain == "day":
        return start + timedelta(days=1)
    if grain == "week":
        return start + timedelta(days=7)
    return date(start.year + start.month // 12, start.month % 12 + 1, 1)


def buckets(window: Window, grain: Grain) -> list[date]:
    """Return the start of every period that overlaps the window."""
    out, current = [], bucket_start(window.start, grain)
    while current <= window.end:
        out.append(current)
        current = next_bucket(current, grain)
    return out


def _measures(ctx: AnalysisContext, rev: str) -> list[str]:
    """Return the SQL select items shared by the KPI and the series queries."""
    r = ctx.roles
    items = [
        "count(*) AS rows",
        f"sum({rev}) AS net_revenue",
        f"sum(CASE WHEN {rev} > 0 THEN {rev} END) AS gross_revenue",
    ]
    if r.has("order_id"):
        oid = r.q("order_id")
        items.append(f"count(DISTINCT CASE WHEN {rev} > 0 THEN {oid} END) AS orders")
    else:
        items.append(f"count(CASE WHEN {rev} > 0 THEN 1 END) AS orders")
    if r.has("quantity"):
        items.append(f"sum({r.q('quantity')}) AS units")
    if r.has("cost"):
        c = r.q("cost")
        items.append(f"sum(CASE WHEN {c} IS NOT NULL THEN {rev} END) AS costed_revenue")
        items.append(f"sum(CASE WHEN {rev} IS NOT NULL THEN {c} END) AS cost")
    return items


def _derive(row: dict[str, Any], ctx: AnalysisContext) -> dict[str, float | None]:
    """Turn one aggregated row into the five headline measures."""
    orders = num(row.get("orders"))
    gross = num(row.get("gross_revenue"))
    margin = None
    if ctx.roles.has("cost"):
        costed = num(row.get("costed_revenue"))
        margin = 100.0 * (costed - num(row.get("cost"))) / costed if costed else None
    return {
        "net_revenue": num(row.get("net_revenue")),
        "orders": orders,
        "aov": gross / orders if orders else None,
        "units": num(row.get("units")) if ctx.roles.has("quantity") else None,
        "margin_pct": margin,
    }


def _clamp(ctx: AnalysisContext, start: date | None, end: date | None) -> tuple[Window, Window]:
    """Return (data window, requested window clamped to the data)."""
    rev, d = ctx.roles.revenue_expr(), ctx.roles.q("date")
    found = ctx.db.one(
        f"SELECT min({d}) AS first, max({d}) AS last FROM data "
        f"WHERE {rev} IS NOT NULL AND {d} IS NOT NULL"
    )
    if found["first"] is None:
        raise SeriesUnavailable("not_enough_data", "No row has both a date and a revenue value.")
    data = Window(as_date(found["first"]), as_date(found["last"]))
    wanted = Window(max(start or data.start, data.start), min(end or data.end, data.end))
    if wanted.start > wanted.end:
        raise SeriesUnavailable(
            "range_outside_data",
            f"The data covers {data.start.isoformat()} to {data.end.isoformat()}; the selected "
            "dates have no rows.",
        )
    return data, wanted


KPI_UNITS = {
    "net_revenue": "money",
    "orders": "count",
    "aov": "money",
    "units": "count",
    "margin_pct": "percent",
}
MISSING_ROLE = {
    "units": "The file has no quantity column, so units cannot be counted.",
    "margin_pct": "The file has no cost column, so margin cannot be computed.",
}


def _kpis(
    now: dict[str, float | None], before: dict[str, float | None] | None, why_not: str | None
) -> list[dict[str, Any]]:
    out = []
    for key, unit in KPI_UNITS.items():
        value, previous = now[key], (before or {}).get(key)
        reason = MISSING_ROLE.get(key) if value is None and key in MISSING_ROLE else None
        change: float | None = None
        if value is not None and previous is not None:
            if unit == "percent":
                change = value - previous
            elif previous:
                change = 100.0 * (value - previous) / abs(previous)
        if change is None and reason is None and value is not None:
            reason = why_not or "The previous window has no value to compare with."
        out.append(
            {
                "key": key,
                "unit": unit,
                "available": value is not None or key not in MISSING_ROLE,
                "value": value,
                "previous_value": previous,
                "change": change,
                "change_unit": "points" if unit == "percent" else "percent",
                "reason": reason,
            }
        )
    return out


def compute(
    ctx: AnalysisContext,
    start: date | None,
    end: date | None,
    grain: Grain,
    max_points: int,
) -> dict[str, Any]:
    """Return KPIs for the range vs the window before it, a filled series and the evidence."""
    if not ctx.roles.has("date"):
        raise SeriesUnavailable(
            "not_enough_data", "The file has no date column, so there is no time series."
        )
    rev = ctx.roles.revenue_expr()
    if rev is None:
        raise SeriesUnavailable(
            "not_enough_data",
            "The file has no revenue column (nor quantity and price), so there is no series.",
        )
    if start and end and end < start:
        raise SeriesUnavailable("invalid_range", "The end date is before the start date.")
    data, window = _clamp(ctx, start, end)
    periods = buckets(window, grain)
    if len(periods) > max_points:
        raise SeriesUnavailable(
            "range_too_long",
            f"That range has {len(periods)} {grain}s; choose a coarser grain or a shorter range "
            f"(at most {max_points} points).",
        )
    previous = Window(window.start - timedelta(days=window.days), window.start - timedelta(days=1))
    d = ctx.roles.q("date")
    items = ", ".join(_measures(ctx, rev))
    kpi_sql = (
        f"SELECT CASE WHEN {d} >= {lit(window.start)} THEN 'current' ELSE 'previous' END "
        f"AS part, {items} FROM data WHERE {rev} IS NOT NULL "
        f"AND {d} BETWEEN {lit(previous.start)} AND {lit(window.end)} GROUP BY 1 ORDER BY 1"
    )
    kpi_rows = {r["part"]: r for r in ctx.db.rows(kpi_sql)}
    current_row = kpi_rows.get("current", {})
    previous_row = kpi_rows.get("previous")
    why_not = None
    if previous.start < data.start:
        why_not = (
            f"The data starts on {data.start.isoformat()}, so the {window.days} days before the "
            "range are not fully covered; no change is shown."
        )
        previous_row = None
    elif previous_row is None:
        why_not = "The window before the range has no sales rows."
    now = _derive(current_row, ctx)
    before = _derive(previous_row, ctx) if previous_row else None

    series_sql = (
        f"SELECT CAST(date_trunc('{grain}', {d}) AS DATE) AS period, {items} FROM data "
        f"WHERE {rev} IS NOT NULL AND {d} BETWEEN {lit(window.start)} AND {lit(window.end)} "
        f"GROUP BY 1 ORDER BY 1"
    )
    found = {as_date(r["period"]): r for r in ctx.db.rows(series_sql)}
    series = []
    for period in periods:
        row = found.get(period, {})
        last_day = next_bucket(period, grain) - timedelta(days=1)
        series.append(
            {
                "period": period.isoformat(),
                **_derive(row, ctx),
                "rows": int(row.get("rows") or 0),
                "partial": period < window.start or last_day > window.end,
            }
        )
    rows = int(current_row.get("rows") or 0)
    warnings = []
    if (start and start < data.start) or (end and end > data.end):
        warnings.append(
            f"The data covers {data.start.isoformat()} to {data.end.isoformat()}; the range was "
            "trimmed to it."
        )
    if any(p["partial"] for p in series):
        warnings.append(
            f"The first or last {grain} is only partly inside the range, so it looks smaller."
        )
    if ctx.roles.derived_revenue:
        warnings.append("Revenue was derived as quantity x price (the file has no revenue column).")
    complete = [p for p in series if not p["partial"]]
    out: dict[str, Any] = jsonable(
        {
            "range": window.as_dict(),
            "data_range": data.as_dict(),
            "previous_range": previous.as_dict(),
            "grain": grain,
            "currency": ctx.currency,
            "kpis": _kpis(now, before, why_not),
            "series": series,
            "sample_size": rows,
            "confidence": series_confidence(len(complete), rows),
            "method": METHOD,
            "warnings": warnings,
            "evidence": [
                evidence_query(
                    "Headline figures for the range and the window before it",
                    kpi_sql,
                    list(kpi_rows.values()),
                ),
                evidence_query(
                    f"Net revenue and orders by {grain}", series_sql, list(found.values())
                ),
            ],
        }
    )
    return out
