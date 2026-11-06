"""The summary an analysis run keeps: the figures alerts compare and reports print.

A later run of the same version deletes this run's insights, so the few figures a comparison needs
are copied here when the run finishes. Nothing is estimated: the KPI block is the dashboard's own
computation (`ml.timeseries.compute`, default range = the last 90 days of data), and every other
field is copied from a stored module result.
"""

from datetime import date, timedelta
from typing import Any

import structlog

from app.ml.base import AnalysisContext
from app.ml.timeseries import SeriesUnavailable, compute
from app.models.dataset import DatasetVersion

logger = structlog.get_logger()

SUMMARY_SCHEMA = 1
# The dashboard's default preset ("90d"): the last 90 days that end on the last day of data.
KPI_WINDOW_DAYS = 90
DEAD_STOCK_NAMES = 200
UNUSUAL_DAYS = 50


def _first(insights: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    return next((i for i in insights if i.get("type") == kind), None)


def kpi_block(ctx: AnalysisContext) -> dict[str, Any]:
    """Return the dashboard's default KPI cards (last 90 days vs the 90 days before)."""
    whole = compute(ctx, None, None, "month", ctx.settings.timeseries_max_points)
    end = date.fromisoformat(whole["data_range"]["end"])
    start = end - timedelta(days=KPI_WINDOW_DAYS - 1)
    found = compute(ctx, start, end, "week", ctx.settings.timeseries_max_points)
    kpis = {k: found[k] for k in ("range", "previous_range", "data_range", "currency", "kpis")}
    kpis.update(
        sample_size=found["sample_size"],
        confidence=found["confidence"],
        warnings=found["warnings"],
        evidence=[
            {k: q.get(k) for k in ("purpose", "sql", "row_count")} for q in found["evidence"]
        ],
    )
    monthly = [
        {
            "period": p["period"],
            "net_revenue": p["net_revenue"],
            "orders": p["orders"],
            "partial": p["partial"],
        }
        for p in whole["series"]
    ]
    return {"kpis": kpis, "monthly": monthly}


def _forecast(insights: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Monthly net-revenue forecast of the total, keyed by month (only monthly revenue counts)."""
    found = _first(insights, "forecast_total")
    if found is None:
        return None
    p = found.get("payload") or {}
    if p.get("frequency") != "month" or p.get("metric") != "revenue":
        return None
    history = (found.get("chart_spec") or {}).get("history") or []
    periods = {
        period: {k: p[k][i] for k in ("point", "lo80", "hi80", "lo95", "hi95")}
        for i, period in enumerate(p.get("periods") or [])
    }
    return {
        "metric": "revenue",
        "model": p.get("model"),
        "made_through": history[-1]["period"] if history else None,
        "no_better_than_baseline": bool(p.get("no_better_than_baseline")),
        "periods": periods,
    }


def _unusual_days(insights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found = _first(insights, "anomaly_summary")
    events = (found or {}).get("payload", {}).get("events")
    if events is None:
        events = [i.get("payload", {}) for i in insights if i.get("type") == "anomaly"]
    keep = ("start", "end", "unit", "kind", "actual", "expected", "change")
    return [{k: e.get(k) for k in keep} for e in events[:UNUSUAL_DAYS]]


def _churn(insights: list[dict[str, Any]]) -> dict[str, Any] | None:
    found = _first(insights, "churn_risk")
    if found is None:
        return None
    p = found.get("payload") or {}
    bands = p.get("bands") or {}
    return {
        "basis": p.get("basis"),
        "scored_customers": p.get("scored_customers"),
        "high": bands.get("high"),
        "top_10pct": bands.get("top_10pct"),
    }


def _dead_stock(insights: list[dict[str, Any]]) -> dict[str, Any] | None:
    found = _first(insights, "dead_stock")
    if found is None:
        return None
    p = found.get("payload") or {}
    return {
        "names": list(p.get("item_names") or [])[:DEAD_STOCK_NAMES],
        "count": p.get("dead_count"),
        "days": p.get("days"),
        "as_of": p.get("as_of"),
    }


def latest_complete_month(monthly: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the last month that lies fully inside the data, or None."""
    complete = [m for m in monthly if not m.get("partial")]
    return complete[-1] if complete else None


def build(
    ctx: AnalysisContext, version: DatasetVersion, insights: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return the run summary from the open analysis table and the run's insights."""
    summary: dict[str, Any] = {
        "schema": SUMMARY_SCHEMA,
        "version": version.version,
        "quality_score": version.quality_score,
        "kpis": None,
        "kpis_reason": None,
        "monthly": [],
        "latest_month": None,
        "last_data_date": None,
        "forecast": _forecast(insights),
        "unusual_days": _unusual_days(insights),
        "churn": _churn(insights),
        "dead_stock": _dead_stock(insights),
    }
    try:
        block = kpi_block(ctx)
    except SeriesUnavailable as exc:
        summary["kpis_reason"] = exc.message
        return summary
    summary.update(block)
    summary["latest_month"] = latest_complete_month(block["monthly"])
    summary["last_data_date"] = block["kpis"]["data_range"]["end"]
    return summary


def from_profile_last_date(version: DatasetVersion) -> date | None:
    """Return the last date in a version's data from its profile (for the freshness check)."""
    found = (version.dataset_profile or {}).get("date_range") or {}
    last = found.get("max")
    if not last:
        return None
    try:
        return date.fromisoformat(str(last)[:10])
    except ValueError:
        return None
