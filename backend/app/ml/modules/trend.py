"""Trend and seasonality: STL, weekday pattern, best/worst months and year-to-date vs last year."""

from datetime import date
from typing import Any

import pandas as pd
from statsmodels.tsa.seasonal import STL

from app.ml.base import AnalysisContext, AnalysisModule, Insight
from app.ml.engine import lit, num
from app.ml.format import fmt_money, fmt_number, fmt_pct
from app.ml.modules.common import (
    MONTHS,
    WEEKDAYS,
    as_date,
    build_insight,
    evidence_query,
    excluded_rows,
    is_complete_period,
    pct_change,
    revenue_expr,
    revenue_key_columns,
    revenue_note,
    series_confidence,
)
from app.services.profiling.results import confidence_for

STL_MIN_MONTHS = 24
SEASON_MIN_MONTHS = 12
WEEKDAY_MIN_DAYS = 28


class TrendSeasonalityModule(AnalysisModule):
    """Seasonality, weekday pattern, growth extremes and year-to-date comparison."""

    name = "trend_seasonality"
    title = "Trend and seasonality"
    requires = frozenset({"revenue", "date"})

    def run(self, context: AnalysisContext) -> list[Insight]:
        """Return the trend insights that the amount of history supports."""
        months = _monthly(context)
        insights: list[Insight | None] = [
            _seasonality(context, months),
            _weekday(context),
            _extremes(context, months),
            _ytd(context),
        ]
        return [i for i in insights if i is not None]


class _Months:
    """Complete monthly net revenue plus the SQL that produced it."""

    def __init__(self, rows: list[dict[str, Any]], sql: str, dropped: date | None) -> None:
        self.sql = sql
        self.rows = rows
        self.dropped = dropped
        self.revenue = {as_date(r["period"]): num(r["revenue"]) for r in rows}
        self.row_count = sum(int(r["rows"]) for r in rows)


def _monthly(ctx: AnalysisContext) -> _Months:
    rev, d = revenue_expr(ctx), ctx.roles.q("date")
    sql = (
        f"SELECT CAST(date_trunc('month', {d}) AS DATE) AS period, sum({rev}) AS revenue, "
        f"count(*) AS rows FROM data WHERE {rev} IS NOT NULL AND {d} IS NOT NULL "
        f"GROUP BY 1 ORDER BY 1"
    )
    rows = ctx.db.rows(sql)
    last = ctx.db.scalar(f"SELECT max({d}) FROM data WHERE {rev} IS NOT NULL AND {d} IS NOT NULL")
    dropped: date | None = None
    if rows and last is not None and not is_complete_period(as_date(last), "month"):
        dropped = as_date(rows.pop()["period"])
    return _Months(rows, sql, dropped)


def _month_name(month: date) -> str:
    return f"{MONTHS[month.month - 1][:3]} {month.year}"


def _keys(ctx: AnalysisContext) -> list[str]:
    return [*revenue_key_columns(ctx), ctx.roles.columns["date"]]


def _seasonality(ctx: AnalysisContext, months: _Months) -> Insight | None:
    n = len(months.revenue)
    if n < SEASON_MIN_MONTHS:
        ctx.warn(
            f"Seasonality needs at least {SEASON_MIN_MONTHS} months of history; "
            f"only {n} complete months found, so it was skipped."
        )
        return None
    first, last = min(months.revenue), max(months.revenue)
    index = pd.date_range(first, last, freq="MS")
    series = pd.Series({pd.Timestamp(k): v for k, v in months.revenue.items()}).reindex(index)
    gaps = int(series.isna().sum())
    warnings: list[str] = []
    if months.dropped is not None:
        warnings.append(f"The incomplete month {_month_name(months.dropped)} was left out.")
    if gaps:
        series = series.interpolate(limit_direction="both")
        warnings.append(f"{gaps} month(s) with no sales were filled by interpolation.")
    overall = float(series.mean())
    raw = series.groupby(series.index.month).mean() / overall
    use_stl = len(series) >= STL_MIN_MONTHS
    strength: float | None = None
    if use_stl:
        fit = STL(series, period=12, robust=True).fit()
        seasonal = fit.seasonal.groupby(fit.seasonal.index.month).mean()
        resid_var = float(fit.resid.var())
        strength = max(0.0, 1.0 - resid_var / float((fit.seasonal + fit.resid).var()))
        score = seasonal
        method = "STL decomposition (statsmodels, period 12, robust) of monthly net revenue"
    else:
        score = raw
        method = "average net revenue per calendar month vs the overall monthly average"
        warnings.append(
            f"Only {n} months of history: STL needs {STL_MIN_MONTHS}, so a simple monthly "
            "average was used instead and confidence is low."
        )
    peak, trough = int(score.idxmax()), int(score.idxmin())
    by_month = series.groupby(series.index.month).mean()
    effect = float(by_month[peak]) - overall
    cur, loc = ctx.currency, ctx.locale
    lift = 100.0 * (float(raw[peak]) - 1.0)
    years = n / 12.0
    confidence = series_confidence(n, months.row_count)
    if not use_stl:
        confidence = "low"
    note = revenue_note(ctx)
    return build_insight(
        ctx,
        module="trend_seasonality",
        type="seasonality",
        title=f"Peak season: {MONTHS[peak - 1]}",
        summary=(
            f"{MONTHS[peak - 1]} is the strongest month: it averages "
            f"{fmt_money(float(by_month[peak]), cur, loc)} of net revenue, {fmt_pct(lift, loc)} "
            f"above the average month. The weakest month is {MONTHS[trough - 1]}. "
            f"Based on {fmt_number(years, loc, 1)} years of data."
        ),
        value=peak,
        sample_size=months.row_count,
        method=method,
        key_columns=_keys(ctx),
        money_effect=effect,
        payload={
            "peak_month": peak,
            "trough_month": trough,
            "peak_month_name": MONTHS[peak - 1],
            "trough_month_name": MONTHS[trough - 1],
            "seasonal_strength": None if strength is None else round(strength, 3),
            "month_index": {str(int(m)): round(float(v), 4) for m, v in raw.items()},
            "seasonal_component": {str(int(m)): round(float(v), 2) for m, v in score.items()}
            if use_stl
            else None,
            "months_observed": n,
            "net_revenue_basis": "net",
        },
        chart_spec={
            "type": "bar",
            "title": "Seasonal index by calendar month (1.0 = average)",
            "data": [
                {"label": MONTHS[int(m) - 1], "value": round(float(v), 4)} for m, v in raw.items()
            ],
        },
        evidence=[evidence_query("Net revenue by calendar month", months.sql, months.rows)],
        excluded=excluded_rows(ctx, _keys(ctx)),
        confidence=confidence,
        warnings=tuple(([note] if note else []) + warnings),
        what="figures",
    )


def _weekday(ctx: AnalysisContext) -> Insight | None:
    rev, d = revenue_expr(ctx), ctx.roles.q("date")
    sql = (
        f"SELECT dayofweek({d}) AS weekday, sum({rev}) AS revenue, count(DISTINCT {d}) AS days, "
        f"count(*) AS rows FROM data WHERE {rev} IS NOT NULL AND {d} IS NOT NULL "
        f"GROUP BY 1 ORDER BY 1"
    )
    rows = ctx.db.rows(sql)
    total_days = sum(int(r["days"]) for r in rows)
    if len(rows) < 5 or total_days < WEEKDAY_MIN_DAYS:
        ctx.warn("The weekday pattern needs daily data over at least 4 weeks; it was skipped.")
        return None
    avg = {int(r["weekday"]): num(r["revenue"]) / int(r["days"]) for r in rows}
    best, worst = max(avg, key=lambda k: (avg[k], -k)), min(avg, key=lambda k: (avg[k], k))
    mean_day = sum(num(r["revenue"]) for r in rows) / total_days
    cur, loc = ctx.currency, ctx.locale
    sample = sum(int(r["rows"]) for r in rows)
    spread = 100.0 * (avg[best] - avg[worst]) / avg[worst] if avg[worst] else 0.0
    return build_insight(
        ctx,
        module="trend_seasonality",
        type="weekday_pattern",
        title=f"Strongest day: {WEEKDAYS[best]}",
        summary=(
            f"{WEEKDAYS[best]} is the best day, averaging {fmt_money(avg[best], cur, loc)} of net "
            f"revenue per trading day; {WEEKDAYS[worst]} is the weakest at "
            f"{fmt_money(avg[worst], cur, loc)} ({fmt_pct(spread, loc)} lower)."
        ),
        value=best,
        sample_size=sample,
        method="average net revenue per trading day, grouped by weekday",
        key_columns=_keys(ctx),
        money_effect=(avg[best] - mean_day) * 52,
        payload={
            "best_weekday": best,
            "worst_weekday": worst,
            "average_by_weekday": {WEEKDAYS[k]: round(v, 2) for k, v in sorted(avg.items())},
        },
        chart_spec={
            "type": "bar",
            "title": "Average net revenue per trading day by weekday",
            "data": [{"label": WEEKDAYS[k], "value": round(v, 2)} for k, v in sorted(avg.items())],
        },
        evidence=[evidence_query("Net revenue and trading days by weekday", sql, rows)],
        excluded=excluded_rows(ctx, _keys(ctx)),
        what="figures",
    )


def _extremes(ctx: AnalysisContext, months: _Months) -> Insight | None:
    ordered = sorted(months.revenue.items())
    changes = []
    for (prev_m, prev_v), (m, v) in zip(ordered, ordered[1:], strict=False):
        consecutive = (m.year * 12 + m.month) - (prev_m.year * 12 + prev_m.month) == 1
        pct = pct_change(v, prev_v)
        if consecutive and prev_v > 0 and pct is not None:
            changes.append((m, pct, v - prev_v))
    if len(ordered) < 3 or not changes:
        ctx.warn("Growth and decline periods need at least three consecutive months; skipped.")
        return None
    up = max(changes, key=lambda c: (c[1], c[0]))
    down = min(changes, key=lambda c: (c[1], c[0]))
    cur, loc = ctx.currency, ctx.locale
    parts = []
    if up[1] > 0:
        parts.append(
            f"The strongest growth was in {_month_name(up[0])}: {fmt_pct(up[1], loc)} "
            f"({fmt_money(up[2], cur, loc)}) vs the month before."
        )
    if down[1] < 0:
        parts.append(
            f"The steepest decline was in {_month_name(down[0])}: {fmt_pct(down[1], loc)} "
            f"({fmt_money(down[2], cur, loc)})."
        )
    if not parts:
        return None
    effect = max(abs(up[2]) if up[1] > 0 else 0.0, abs(down[2]) if down[1] < 0 else 0.0)
    return build_insight(
        ctx,
        module="trend_seasonality",
        type="growth_extremes",
        title="Strongest growth and steepest decline",
        summary=" ".join(parts),
        value={"growth": _change(up), "decline": _change(down)},
        sample_size=months.row_count,
        method="month-over-month change of net revenue between consecutive complete months",
        key_columns=_keys(ctx),
        money_effect=effect,
        payload={"strongest_growth": _change(up), "steepest_decline": _change(down)},
        chart_spec={},
        evidence=[evidence_query("Net revenue by month", months.sql, months.rows)],
        excluded=excluded_rows(ctx, _keys(ctx)),
        confidence=series_confidence(len(ordered), months.row_count),
        what="figures",
    )


def _change(item: tuple[date, float, float]) -> dict[str, Any]:
    return {"month": item[0].isoformat(), "pct": round(item[1], 2), "change": round(item[2], 2)}


def _same_day_last_year(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return date(day.year - 1, 2, 28)


def _ytd(ctx: AnalysisContext) -> Insight | None:
    rev, d = revenue_expr(ctx), ctx.roles.q("date")
    bounds = ctx.db.one(
        f"SELECT min({d}) AS first, max({d}) AS last FROM data "
        f"WHERE {rev} IS NOT NULL AND {d} IS NOT NULL"
    )
    if bounds["last"] is None:
        return None
    last, first = as_date(bounds["last"]), as_date(bounds["first"])
    year = last.year
    if first > date(year - 1, 1, 7):
        ctx.warn(
            "Year-to-date vs last year needs data from the start of the previous year; skipped."
        )
        return None
    start, prior_start = date(year, 1, 1), date(year - 1, 1, 1)
    prior_end = _same_day_last_year(last)
    sql = (
        f"SELECT sum(CASE WHEN {d} >= {lit(start)} AND {d} <= {lit(last)} THEN {rev} END) "
        f"AS ytd, sum(CASE WHEN {d} >= {lit(prior_start)} AND {d} <= {lit(prior_end)} "
        f"THEN {rev} END) AS prior_ytd, count(*) AS rows FROM data "
        f"WHERE {rev} IS NOT NULL AND {d} IS NOT NULL"
    )
    row = ctx.db.one(sql)
    ytd, prior = num(row["ytd"]), num(row["prior_ytd"])
    change = pct_change(ytd, prior)
    if change is None:
        ctx.warn("There was no revenue in the same period last year; year-to-date skipped.")
        return None
    cur, loc = ctx.currency, ctx.locale
    direction = "ahead of" if ytd >= prior else "behind"
    return build_insight(
        ctx,
        module="trend_seasonality",
        type="ytd_vs_prior_year",
        title=f"Year to date is {fmt_pct(abs(change), loc)} {direction} last year",
        summary=(
            f"Net revenue from {start.isoformat()} to {last.isoformat()} is "
            f"{fmt_money(ytd, cur, loc)}, versus {fmt_money(prior, cur, loc)} for "
            f"{prior_start.isoformat()} to {prior_end.isoformat()} ({fmt_pct(change, loc)})."
        ),
        value=round(change, 2),
        sample_size=int(row["rows"]),
        method="net revenue year-to-date vs the same span of the previous year",
        key_columns=_keys(ctx),
        money_effect=ytd - prior,
        payload={
            "ytd": round(ytd, 2),
            "prior_ytd": round(prior, 2),
            "change_pct": round(change, 2),
            "period": {"start": start, "end": last},
            "prior_period": {"start": prior_start, "end": prior_end},
        },
        chart_spec={},
        evidence=[evidence_query("Year-to-date and prior-year-to-date net revenue", sql, [row])],
        excluded=excluded_rows(ctx, _keys(ctx)),
        confidence=confidence_for(int(row["rows"])),
        what="figures",
    )
