"""Anomalies: unusual days (weeks, months), lasting level shifts, likely contributors, odd rows."""

import math
from datetime import date, timedelta
from typing import Any

import numpy as np

from app.ml.anomaly import detect
from app.ml.anomaly.rows import discount_rate_expr, row_anomalies
from app.ml.base import AnalysisContext, AnalysisModule, DataFacts, Insight
from app.ml.budget import Budget
from app.ml.engine import lit, num
from app.ml.format import fmt_money, fmt_number, fmt_pct
from app.ml.modules.common import (
    as_date,
    build_insight,
    evidence_query,
    excluded_rows,
    refund_rows,
    revenue_expr,
    revenue_key_columns,
    revenue_note,
)
from app.ml.roles import DIMENSION_ROLES, RoleMap

TOP_EVENTS = 5
CONTRIBUTORS = 3
BASELINE_PERIODS = {"day": 28, "week": 4, "month": 3}
WINDOW = {"day": 14, "week": 6, "month": 4}
UNIT_BY_GRANULARITY = {
    None: "day",
    "daily": "day",
    "irregular": "day",
    "weekly": "week",
    "monthly": "month",
}
NOT_CAUSES = "These are findings from the data, not proven causes."


class AnomalyModule(AnalysisModule):
    """Find unusual periods and lasting shifts in net revenue, and what drove them."""

    name = "anomalies"
    title = "Anomaly detection"
    requires = frozenset({"date", "revenue"})

    def data_requirement(self, facts: DataFacts) -> str | None:
        """Explain when the history is too short or too coarse for any method."""
        unit = UNIT_BY_GRANULARITY.get(facts.granularity, "")
        if not unit:
            return (
                f"the dates are {facts.granularity}; anomaly detection needs daily, weekly or "
                "monthly data"
            )
        if facts.date_span_days is None:
            return None
        days = facts.date_span_days + 1
        if unit == "day" and days < detect.STL_MIN_DAYS:
            return (
                f"only {days} days of history; anomaly detection needs at least "
                f"{detect.STL_MIN_DAYS} days"
            )
        per = {"day": 1, "week": 7, "month": 30.4}[unit]
        if unit != "day" and days / per < detect.COARSE_MIN_POINTS - 1:
            return (
                f"only about {math.floor(days / per)} {unit}s of history; anomaly detection "
                f"needs at least {detect.COARSE_MIN_POINTS} {unit}s"
            )
        return None

    def run(self, context: AnalysisContext) -> list[Insight]:
        """Return the anomaly, level-shift, summary and row-level insights."""
        ctx = context
        unit = UNIT_BY_GRANULARITY.get(ctx.granularity, "")
        if not unit:
            ctx.warn("The dates are too coarse for anomaly detection; skipped.")
            return []
        series = _series(ctx, unit)
        if series is None:
            ctx.warn("No row has both a date and revenue; anomaly detection was skipped.")
            return []
        plan = detect.plan_for(unit, len(series["periods"]))
        if plan is None:
            ctx.warn(
                f"Only {len(series['periods'])} {unit}s of history; anomaly detection needs at "
                f"least {detect.STL_MIN_DAYS if unit == 'day' else detect.COARSE_MIN_POINTS}."
            )
            return []
        budget = Budget(ctx.settings.anomaly_max_seconds, float("inf"))
        warnings = _basis_warnings(ctx)
        if plan.forest and budget.exhausted():
            plan = detect.MethodPlan(plan.unit, plan.baseline, False, plan.period)
            warnings.append(
                "The time limit was reached, so the Isolation Forest check was skipped and "
                "every anomaly rests on the robust z-score alone."
            )
        scored = detect.score(
            series["revenue"], plan, _extra_features(series), ctx.settings.ml_seed
        )
        found = detect.events(scored)
        insights: list[Insight] = []
        for event in found[:TOP_EVENTS]:
            insights.append(_event_insight(ctx, series, scored, event, found, plan, warnings))
        shifts = _level_shifts(ctx, series, unit)
        insights.extend(_shift_insight(ctx, series, s, warnings) for s in shifts)
        insights.append(_summary_insight(ctx, series, scored, found, shifts, plan, warnings))
        if budget.exhausted():
            ctx.warn("The time limit was reached, so row-level checks were skipped.")
        else:
            rows = row_anomalies(ctx)
            if rows is not None:
                insights.append(rows)
        return insights


# ---- series ------------------------------------------------------------------------------


def _orders_expr(roles: RoleMap, rev: str) -> str:
    if roles.has("order_id"):
        return f"count(DISTINCT {roles.q('order_id')}) FILTER (WHERE {rev} > 0)"
    return f"count(*) FILTER (WHERE {rev} > 0)"


def _series(ctx: AnalysisContext, unit: str) -> dict[str, Any] | None:
    """Aggregate revenue, orders, units and discount per period in DuckDB and fill the gaps."""
    rev, d = revenue_expr(ctx), ctx.roles.q("date")
    period = d if unit == "day" else f"CAST(date_trunc('{unit}', {d}) AS DATE)"
    columns = [
        f"{period} AS period",
        f"sum({rev}) AS revenue",
        f"{_orders_expr(ctx.roles, rev)} AS orders",
    ]
    if ctx.roles.has("quantity"):
        qty = ctx.roles.q("quantity")
        columns.append(f"coalesce(sum({qty}) FILTER (WHERE {qty} > 0), 0) AS units")
    rate = discount_rate_expr(ctx)
    if rate is not None:
        columns.append(f"coalesce(avg({rate}), 0) AS discount_rate")
    select = ", ".join(columns)
    # the unit is the data's own spacing (daily data is never rolled up), so no period is partial
    sql = (
        f"SELECT {select} FROM data WHERE {d} IS NOT NULL AND {rev} IS NOT NULL "
        f"GROUP BY 1 ORDER BY 1"
    )
    rows = ctx.db.rows(sql)
    if not rows:
        return None
    by_period = {as_date(r["period"]): r for r in rows}
    periods = _calendar(min(by_period), max(by_period), unit)
    names = [k for k in rows[0] if k != "period"]
    values = {
        k: np.array([num(by_period[p][k]) if p in by_period else 0.0 for p in periods])
        for k in names
    }
    return {"unit": unit, "periods": periods, "select": select, "sql": sql, "rows": rows, **values}


def _calendar(first: date, last: date, unit: str) -> list[date]:
    out, current = [], first
    while current <= last:
        out.append(current)
        current = _step(current, unit, 1)
    return out


def _step(day: date, unit: str, steps: int) -> date:
    if unit == "day":
        return day + timedelta(days=steps)
    if unit == "week":
        return day + timedelta(weeks=steps)
    total = day.year * 12 + day.month - 1 + steps
    return date(total // 12, total % 12 + 1, 1)


def _extra_features(series: dict[str, Any]) -> dict[str, np.ndarray]:
    revenue, orders = series["revenue"], series["orders"]
    extra = {
        "orders": orders,
        "average order value": np.where(orders > 0, revenue / np.maximum(orders, 1), 0.0),
    }
    if "units" in series:
        extra["units"] = series["units"]
    if "discount_rate" in series:
        extra["discount rate"] = series["discount_rate"]
    return extra


def _basis_warnings(ctx: AnalysisContext) -> list[str]:
    warnings = []
    refunds = refund_rows(ctx)
    if refunds:
        warnings.append(
            f"Revenue is net: {fmt_number(refunds, ctx.locale)} refund rows stay in as negative "
            "amounts, so a day of large refunds can show as a drop."
        )
    note = revenue_note(ctx)
    if note:
        warnings.append(note)
    return warnings


def _confidence(plan: detect.MethodPlan, periods: int) -> str:
    per_year = {"day": 365, "week": 52, "month": 12}[plan.unit]
    if plan.forest and periods >= per_year:
        return "high"
    return "medium" if plan.forest else "low"


def _keys(ctx: AnalysisContext) -> list[str]:
    return [ctx.roles.columns["date"], *revenue_key_columns(ctx)]


# ---- one anomaly -------------------------------------------------------------------------


def _event_insight(
    ctx: AnalysisContext,
    series: dict[str, Any],
    scored: detect.Scored,
    event: detect.Event,
    found: list[detect.Event],
    plan: detect.MethodPlan,
    warnings: list[str],
) -> Insight:
    unit, periods = series["unit"], series["periods"]
    start, end = periods[event.start], periods[event.end]
    flagged = {periods[i] for e in found for i in range(e.start, e.end + 1)}
    drill = _drill_down(ctx, unit, start, end, flagged, event.kind)
    cur, loc = ctx.currency, ctx.locale
    when = start.isoformat() if start == end else f"{start.isoformat()} to {end.isoformat()}"
    ratio = event.actual / event.expected if event.expected > 0 else None
    size = (
        f"{fmt_number(ratio, loc, 1)}x the expected level"
        if ratio is not None and event.kind == "spike"
        else f"{fmt_pct(100.0 * (1 - ratio), loc)} below the expected level"
        if ratio is not None
        else "far from the expected level"
    )
    contributors = drill["text"]
    summary = (
        f"Net revenue {'jumped' if event.kind == 'spike' else 'fell'} on {when}: "
        f"{fmt_money(event.actual, cur, loc)} against an expected "
        f"{fmt_money(event.expected, cur, loc)} "
        f"({size}, robust z {fmt_number(event.peak_z, loc, 1)})."
        + (f" {contributors}" if contributors else "")
        + f" {NOT_CAUSES}"
    )
    lo = max(0, event.start - WINDOW[unit])
    hi = min(len(periods), event.end + WINDOW[unit] + 1)
    window = [
        {
            "period": periods[i].isoformat(),
            "actual": round(float(scored.actual[i]), 2),
            "expected": round(float(scored.expected[i]), 2),
            "flagged": bool(scored.flagged[i]),
        }
        for i in range(lo, hi)
    ]
    d = ctx.roles.q("date")
    window_sql = (
        f"SELECT {series['select']} FROM data "
        f"WHERE {d} IS NOT NULL AND {revenue_expr(ctx)} IS NOT NULL "
        f"AND {d} >= {lit(periods[lo])} AND {d} < {lit(_step(periods[hi - 1], unit, 1))} "
        f"GROUP BY 1 ORDER BY 1"
    )
    evidence = [
        evidence_query(
            f"Revenue per {unit} around the anomaly", window_sql, ctx.db.rows(window_sql)
        )
    ]
    evidence += drill["evidence"]
    return build_insight(
        ctx,
        module="anomalies",
        type="anomaly",
        title=f"Revenue {event.kind} on {when}: {size}",
        summary=summary,
        value=round(event.change, 2),
        sample_size=len(periods),
        method=scored.method,
        key_columns=_keys(ctx),
        money_effect=event.change,
        payload={
            "start": start.isoformat(),
            "end": end.isoformat(),
            "unit": unit,
            "kind": event.kind,
            "length": event.length,
            "actual": round(event.actual, 2),
            "expected": round(event.expected, 2),
            "change": round(event.change, 2),
            "peak_z": round(event.peak_z, 2),
            "methods": event.methods,
            "likely_contributors": drill["contributors"],
            "baseline": drill["baseline"],
            "window": window,
            "finding_not_cause": True,
        },
        chart_spec={
            "type": "line",
            "title": f"Net revenue per {unit}: actual vs expected",
            "x": "period",
            "y": ["actual", "expected"],
            "markers": [{"period": start.isoformat(), "kind": event.kind}],
            "data": "payload.window",
        },
        evidence=evidence,
        excluded=excluded_rows(ctx, _keys(ctx)),
        confidence=_confidence(plan, len(periods)),  # type: ignore[arg-type]
        warnings=tuple(warnings),
        what="figures",
    )


def _drill_down(
    ctx: AnalysisContext, unit: str, start: date, end: date, flagged: set[date], kind: str
) -> dict[str, Any]:
    """Compare each dimension value during the anomaly with the periods just before it."""
    base_start = _step(start, unit, -BASELINE_PERIODS[unit])
    base_periods = [
        p for p in _calendar(base_start, _step(start, unit, -1), unit) if p not in flagged
    ]
    event_periods = len(_calendar(start, end, unit))
    result: dict[str, Any] = {
        "contributors": {},
        "evidence": [],
        "text": "",
        "baseline": {
            "from": base_start.isoformat(),
            "to": _step(start, unit, -1).isoformat(),
            "periods": len(base_periods),
        },
    }
    if not base_periods:
        return result
    rev, d = revenue_expr(ctx), ctx.roles.q("date")
    excluded = sorted(p for p in flagged if base_start <= p < start)
    skip = (
        f" AND {d} NOT IN ({', '.join(lit(p) for p in excluded)})"
        if excluded and unit == "day"
        else ""
    )
    end_excl = _step(end, unit, 1)
    order = "DESC" if kind == "spike" else "ASC"
    best: tuple[float, str, list[dict[str, Any]]] | None = None
    for role in DIMENSION_ROLES:
        if not ctx.roles.has(role):
            continue
        dim = ctx.roles.q(role)
        sql = (
            f"WITH event AS (SELECT {dim} AS item, sum({rev}) AS revenue FROM data "
            f"WHERE {d} >= {lit(start)} AND {d} < {lit(end_excl)} AND {dim} IS NOT NULL "
            f"GROUP BY 1), "
            f"base AS (SELECT {dim} AS item, sum({rev}) AS revenue FROM data "
            f"WHERE {d} >= {lit(base_start)} AND {d} < {lit(start)}{skip} AND {dim} IS NOT NULL "
            f"GROUP BY 1) "
            f"SELECT coalesce(event.item, base.item) AS item, "
            f"round(coalesce(event.revenue, 0) / {event_periods}, 2) AS per_{unit}_during, "
            f"round(coalesce(base.revenue, 0) / {len(base_periods)}, 2) AS per_{unit}_before, "
            f"round(coalesce(event.revenue, 0) / {event_periods} - "
            f"coalesce(base.revenue, 0) / {len(base_periods)}, 2) AS change_per_{unit} "
            f"FROM event FULL OUTER JOIN base ON event.item = base.item "
            f"ORDER BY change_per_{unit} {order}, item"
        )
        rows = ctx.db.rows(sql)
        if not rows:
            continue
        total = sum(num(r[f"change_per_{unit}"]) for r in rows)
        listed: list[dict[str, Any]] = []
        for r in rows[:CONTRIBUTORS]:
            change = num(r[f"change_per_{unit}"])
            if (change > 0) != (kind == "spike") or change == 0:
                break
            listed.append(
                {
                    "item": str(r["item"]),
                    "change_per_period": round(change, 2),
                    "share_of_change_pct": round(100.0 * change / total, 1) if total else None,
                }
            )
        result["contributors"][role] = listed
        result["evidence"].append(
            evidence_query(f"Likely contributors by {role}: during vs before", sql, rows)
        )
        top_share = listed[0]["share_of_change_pct"] if listed else None
        if listed and top_share is not None and (best is None or top_share > best[0]):
            best = (top_share, role, listed)
    if best is not None:
        _, role, listed = best
        loc, cur = ctx.locale, ctx.currency
        parts = [
            f"{c['item']} ({'+' if c['change_per_period'] > 0 else ''}"
            f"{fmt_money(c['change_per_period'], cur, loc)} a {unit}"
            + (
                f", {fmt_pct(c['share_of_change_pct'], loc)} of the change"
                if c["share_of_change_pct"] is not None
                else ""
            )
            + ")"
            for c in listed
        ]
        result["text"] = f"Likely contributors by {role}: {'; '.join(parts)}."
    return result


# ---- level shifts ------------------------------------------------------------------------


def _weekly(series: dict[str, Any], values: np.ndarray) -> tuple[list[date], np.ndarray]:
    """Sum daily values into complete Monday-to-Sunday weeks."""
    periods: list[date] = series["periods"]
    first = next((i for i, p in enumerate(periods) if p.weekday() == 0), None)
    if first is None:
        return [], np.array([])
    weeks = (len(periods) - first) // 7
    sums = values[first : first + weeks * 7].reshape(weeks, 7).sum(axis=1)
    return [periods[first + 7 * w] for w in range(weeks)], sums


def _level_shifts(
    ctx: AnalysisContext, series: dict[str, Any], unit: str
) -> list[tuple[str, detect.LevelShift, list[date]]]:
    """Level shifts in weekly orders and in weekly (big-order-capped) revenue."""
    if unit != "day":
        return []
    found: list[tuple[str, detect.LevelShift, list[date]]] = []
    for name, values in (
        ("orders", series["orders"]),
        ("revenue", detect.winsorize_daily(series["revenue"])),
    ):
        weeks, sums = _weekly(series, values)
        if len(sums) < detect.LEVEL_SHIFT_MIN_WEEKS:
            if name == "orders":
                ctx.warn(
                    f"Level shifts need {detect.LEVEL_SHIFT_MIN_WEEKS} complete weeks of history "
                    f"(found {len(sums)}), so they were not checked."
                )
            return []
        for shift in detect.level_shifts(sums):
            if not any(abs(shift.week - other.week) <= 2 for _, other, _ in found):
                found.append((name, shift, weeks))
    return found


def _shift_insight(
    ctx: AnalysisContext,
    series: dict[str, Any],
    found: tuple[str, detect.LevelShift, list[date]],
    warnings: list[str],
) -> Insight:
    measure, shift, weeks = found
    start = weeks[shift.week]
    loc, cur = ctx.locale, ctx.currency
    rev, d = revenue_expr(ctx), ctx.roles.q("date")
    sql = (
        f"SELECT CAST(date_trunc('week', {d}) AS DATE) AS week, sum({rev}) AS revenue, "
        f"{_orders_expr(ctx.roles, rev)} AS orders FROM data "
        f"WHERE {d} IS NOT NULL AND {rev} IS NOT NULL AND {d} >= {lit(weeks[0])} "
        f"AND {d} < {lit(weeks[-1] + timedelta(days=7))} GROUP BY 1 ORDER BY 1"
    )
    rows = ctx.db.rows(sql)
    after_end = weeks[min(len(weeks) - 1, shift.week + shift.weeks_after - 1)] + timedelta(days=7)
    after_revenue = sum(num(r["revenue"]) for r in rows if start <= as_date(r["week"]) < after_end)
    change = shift.change_pct / 100.0
    money = after_revenue * change / (1 + change) if change > -1 else -after_revenue
    direction = "up" if change > 0 else "down"
    summary = (
        f"Weekly {measure} stepped {direction} by about {fmt_pct(abs(shift.change_pct), loc)} "
        f"from the week of {start.isoformat()} and stayed at the new level for "
        f"{shift.weeks_after} weeks (compared with the same weeks a year earlier, so the normal "
        f"season is taken out). That is roughly {fmt_money(money, cur, loc)} of revenue since. "
        f"{NOT_CAUSES}"
    )
    return build_insight(
        ctx,
        module="anomalies",
        type="level_shift",
        title=f"Lasting {direction}ward shift in {measure} from {start.isoformat()}",
        summary=summary,
        value=round(shift.change_pct, 1),
        sample_size=len(weeks),
        method=(
            "CUSUM split (binary segmentation) of year-over-year log ratios of weekly "
            f"{measure}; t-like statistic {shift.statistic:.1f} >= {detect.LEVEL_SHIFT_MIN_STAT}, "
            f"change >= {int(detect.LEVEL_SHIFT_MIN_CHANGE * 100)}%, 4+ weeks on each side"
        ),
        key_columns=_keys(ctx),
        money_effect=money,
        payload={
            "kind": shift.kind,
            "measure": measure,
            "start": start.isoformat(),
            "change_pct": round(shift.change_pct, 1),
            "statistic": round(shift.statistic, 2),
            "weeks_after": shift.weeks_after,
            "finding_not_cause": True,
        },
        chart_spec={
            "type": "line",
            "title": f"Weekly {measure}",
            "x": "week",
            "y": measure,
            "markers": [{"period": start.isoformat(), "kind": shift.kind}],
        },
        evidence=[evidence_query("Weekly revenue and orders", sql, rows)],
        excluded=excluded_rows(ctx, _keys(ctx)),
        confidence="high" if shift.statistic >= 2 * detect.LEVEL_SHIFT_MIN_STAT else "medium",
        warnings=tuple(warnings),
    )


# ---- summary -----------------------------------------------------------------------------


def _summary_insight(
    ctx: AnalysisContext,
    series: dict[str, Any],
    scored: detect.Scored,
    found: list[detect.Event],
    shifts: list[tuple[str, detect.LevelShift, list[date]]],
    plan: detect.MethodPlan,
    warnings: list[str],
) -> Insight:
    unit, periods = series["unit"], series["periods"]
    loc = ctx.locale
    spikes = sum(1 for e in found if e.kind == "spike")
    drops = len(found) - spikes
    watch = detect.single_method_points(scored)
    events_payload = [
        {
            "start": periods[e.start].isoformat(),
            "end": periods[e.end].isoformat(),
            "kind": e.kind,
            "length": e.length,
            "actual": round(e.actual, 2),
            "expected": round(e.expected, 2),
            "change": round(e.change, 2),
            "peak_z": round(e.peak_z, 2),
            "methods": e.methods,
        }
        for e in found
    ]
    rule = (
        "both the robust z-score and the Isolation Forest flag it"
        if plan.forest
        else "its robust z-score is 3.5 or more (too little history for the Isolation Forest, "
        "so confidence is low)"
    )
    summary = (
        f"{len(found)} unusual {unit}s in {fmt_number(len(periods), loc)} {unit}s of history "
        f"({spikes} spikes, {drops} drops)"
        + (
            f" and {len(shifts)} lasting level shift{'s' if len(shifts) != 1 else ''}"
            if shifts
            else ""
        )
        + f". A {unit} counts as an anomaly when {rule}. {NOT_CAUSES}"
    )
    change = sum(abs(e.change) for e in found)
    return build_insight(
        ctx,
        module="anomalies",
        type="anomaly_summary",
        title=f"{len(found)} unusual {unit}{'s' if len(found) != 1 else ''} found",
        summary=summary,
        value=len(found),
        sample_size=len(periods),
        method=scored.method,
        key_columns=_keys(ctx),
        money_effect=change,
        payload={
            "unit": unit,
            "periods": len(periods),
            "first": periods[0].isoformat(),
            "last": periods[-1].isoformat(),
            "scale": scored.scale,
            "forest": plan.forest,
            "events": events_payload,
            "level_shifts": [
                {
                    "measure": m,
                    "start": w[s.week].isoformat(),
                    "kind": s.kind,
                    "change_pct": round(s.change_pct, 1),
                }
                for m, s, w in shifts
            ],
            "watch_list": {k: [periods[i].isoformat() for i in v] for k, v in watch.items()},
            "series": [
                {
                    "period": periods[i].isoformat(),
                    "actual": round(float(scored.actual[i]), 2),
                    "expected": round(float(scored.expected[i]), 2),
                }
                for i in range(len(periods))
            ],
            "finding_not_cause": True,
        },
        chart_spec={
            "type": "line",
            "title": f"Net revenue per {unit} with anomalies marked",
            "x": "period",
            "y": ["actual", "expected"],
            "markers": [{"period": e["start"], "kind": e["kind"]} for e in events_payload],
            "data": "payload.series",
        },
        evidence=[
            evidence_query(f"Revenue, orders and more per {unit}", series["sql"], series["rows"])
        ],
        excluded=excluded_rows(ctx, _keys(ctx)),
        confidence=_confidence(plan, len(periods)),  # type: ignore[arg-type]
        warnings=tuple(warnings),
    )
