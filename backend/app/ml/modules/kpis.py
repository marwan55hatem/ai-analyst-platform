"""KPIs: net/gross revenue, returns, orders, AOV, units, margin and period-over-period growth."""

from typing import Any

from app.ml.base import AnalysisContext, AnalysisModule, Insight
from app.ml.engine import num
from app.ml.format import fmt_money, fmt_number, fmt_pct
from app.ml.modules.common import (
    SENSITIVITY,
    as_date,
    build_insight,
    evidence_query,
    excluded_rows,
    is_complete_period,
    pct_change,
    period_unit,
    revenue_expr,
    revenue_key_columns,
    revenue_note,
    series_confidence,
    shift_period,
)

METHOD = "SQL aggregation in DuckDB; headline revenue is net (sales minus returns/refunds)"


class KpiModule(AnalysisModule):
    """Headline numbers and growth for the whole dataset."""

    name = "kpis"
    title = "Key performance indicators"
    requires = frozenset({"revenue"})

    def run(self, context: AnalysisContext) -> list[Insight]:
        """Return the KPI summary, a returns insight and (with dates) a growth insight."""
        totals = _totals(context)
        if totals["rows"] == 0:
            context.warn("No row has a revenue value, so no KPI was computed.")
            return []
        insights = [_summary(context, totals)]
        if totals["returns_count"]:
            insights.append(_returns(context, totals))
        if context.roles.has("date"):
            growth = _growth(context)
            if growth is not None:
                insights.append(growth)
        return insights


def _totals(ctx: AnalysisContext) -> dict[str, Any]:
    rev = revenue_expr(ctx)
    r = ctx.roles
    sql = (
        f"SELECT count(*) AS rows, sum({rev}) AS net_revenue, "
        f"sum(CASE WHEN {rev} > 0 THEN {rev} END) AS gross_revenue, "
        f"sum(CASE WHEN {rev} < 0 THEN -{rev} END) AS returns_total, "
        f"count(CASE WHEN {rev} < 0 THEN 1 END) AS returns_count, "
        f"count(CASE WHEN {rev} > 0 THEN 1 END) AS sale_rows "
        f"FROM data WHERE {rev} IS NOT NULL"
    )
    row = ctx.db.one(sql)
    queries = [evidence_query("Revenue totals: net, gross sales and returns/refunds", sql, [row])]
    out: dict[str, Any] = {
        "rows": int(row["rows"]),
        "net": num(row["net_revenue"]),
        "gross": num(row["gross_revenue"]),
        "returns_total": num(row["returns_total"]),
        "returns_count": int(row["returns_count"]),
        "sale_rows": int(row["sale_rows"]),
        "orders": int(row["sale_rows"]),
        "orders_basis": "each sales row counted as one order",
        "units": None,
        "margin_pct": None,
        "queries": queries,
    }
    if r.has("order_id"):
        oid = r.q("order_id")
        sql = (
            f"SELECT count(DISTINCT {oid}) AS orders FROM data "
            f"WHERE {rev} > 0 AND {oid} IS NOT NULL"
        )
        found = ctx.db.one(sql)
        out["orders"] = int(found["orders"])
        out["orders_basis"] = "distinct order ids with positive revenue"
        queries.append(evidence_query("Distinct orders with positive revenue", sql, [found]))
    if r.has("quantity"):
        q = r.q("quantity")
        sql = (
            f"SELECT sum({q}) AS net_units, sum(CASE WHEN {q} > 0 THEN {q} END) AS gross_units, "
            f"sum(CASE WHEN {q} < 0 THEN -{q} END) AS returned_units "
            f"FROM data WHERE {q} IS NOT NULL"
        )
        found = ctx.db.one(sql)
        out["units"] = {
            "net": num(found["net_units"]),
            "gross": num(found["gross_units"]),
            "returned": num(found["returned_units"]),
        }
        queries.append(evidence_query("Units sold and returned", sql, [found]))
    if r.has("cost"):
        c = r.q("cost")
        sql = (
            f"SELECT sum({rev}) AS revenue, sum({c}) AS cost FROM data "
            f"WHERE {rev} IS NOT NULL AND {c} IS NOT NULL"
        )
        found = ctx.db.one(sql)
        revenue, cost = num(found["revenue"]), num(found["cost"])
        if revenue:
            out["margin_pct"] = 100.0 * (revenue - cost) / revenue
            out["cost"] = cost
        queries.append(evidence_query("Revenue and cost on rows that have both", sql, [found]))
    return out


def _summary(ctx: AnalysisContext, t: dict[str, Any]) -> Insight:
    cur, loc = ctx.currency, ctx.locale
    aov = t["gross"] / t["orders"] if t["orders"] else 0.0
    parts = [
        f"Net revenue is {fmt_money(t['net'], cur, loc)} from {fmt_number(t['orders'], loc)} "
        f"orders (average order value {fmt_money(aov, cur, loc)}, based on gross sales of "
        f"{fmt_money(t['gross'], cur, loc)})."
    ]
    if t["returns_count"]:
        parts.append(
            f"Returns/refunds total {fmt_money(t['returns_total'], cur, loc)} "
            f"({fmt_number(t['returns_count'], loc)} rows) and are already subtracted."
        )
    if t["units"] is not None:
        parts.append(f"Net units sold: {fmt_number(t['units']['net'], loc)}.")
    if t["margin_pct"] is not None:
        parts.append(f"Gross margin is {fmt_pct(t['margin_pct'], loc)}.")
    note = revenue_note(ctx)
    keys = revenue_key_columns(ctx)
    return build_insight(
        ctx,
        module="kpis",
        type="kpi_summary",
        title=f"Net revenue: {fmt_money(t['net'], cur, loc)}",
        summary=" ".join(parts),
        value=round(t["net"], 2),
        sample_size=t["rows"],
        method=METHOD,
        key_columns=keys,
        money_effect=t["net"] * SENSITIVITY,
        payload={
            "revenue_basis": "net",
            "net_revenue": round(t["net"], 2),
            "gross_revenue": round(t["gross"], 2),
            "returns_total": round(t["returns_total"], 2),
            "returns_count": t["returns_count"],
            "orders": t["orders"],
            "orders_basis": t["orders_basis"],
            "average_order_value": round(aov, 2),
            "units": t["units"],
            "margin_pct": None if t["margin_pct"] is None else round(t["margin_pct"], 2),
            "derived_revenue": ctx.roles.derived_revenue,
            "currency": cur,
        },
        chart_spec={
            "type": "bar",
            "title": "Gross sales, returns and net revenue",
            "data": [
                {"label": "Gross sales", "value": round(t["gross"], 2)},
                {"label": "Returns/refunds", "value": round(-t["returns_total"], 2)},
                {"label": "Net revenue", "value": round(t["net"], 2)},
            ],
        },
        evidence=t["queries"],
        excluded=excluded_rows(ctx, keys),
        warnings=(note,) if note else (),
        what="totals",
    )


def _returns(ctx: AnalysisContext, t: dict[str, Any]) -> Insight:
    cur, loc = ctx.currency, ctx.locale
    share = 100.0 * t["returns_total"] / t["gross"] if t["gross"] else 0.0
    keys = revenue_key_columns(ctx)
    return build_insight(
        ctx,
        module="kpis",
        type="returns",
        title=f"Returns/refunds: {fmt_money(t['returns_total'], cur, loc)}",
        summary=(
            f"{fmt_number(t['returns_count'], loc)} rows are returns or refunds worth "
            f"{fmt_money(t['returns_total'], cur, loc)}, {fmt_pct(share, loc)} of gross sales "
            f"({fmt_money(t['gross'], cur, loc)}). Headline revenue is net of them; Pareto and "
            "cohort results use positive sales only."
        ),
        value=round(t["returns_total"], 2),
        sample_size=t["rows"],
        method=METHOD,
        key_columns=keys,
        money_effect=t["returns_total"],
        payload={
            "returns_count": t["returns_count"],
            "returns_total": round(t["returns_total"], 2),
            "gross_revenue": round(t["gross"], 2),
            "net_revenue": round(t["net"], 2),
            "returns_share_pct": round(share, 2),
        },
        chart_spec={},
        evidence=t["queries"][:1],
        excluded=excluded_rows(ctx, keys),
        what="figures",
    )


def _growth(ctx: AnalysisContext) -> Insight | None:
    unit, per_year = period_unit(ctx.granularity)
    rev, d = revenue_expr(ctx), ctx.roles.q("date")
    sql = (
        f"SELECT CAST(date_trunc('{unit}', {d}) AS DATE) AS period, sum({rev}) AS revenue, "
        f"count(*) AS rows FROM data WHERE {rev} IS NOT NULL AND {d} IS NOT NULL "
        f"GROUP BY 1 ORDER BY 1"
    )
    series = ctx.db.rows(sql)
    last_day = as_date(
        ctx.db.scalar(f"SELECT max({d}) FROM data WHERE {rev} IS NOT NULL AND {d} IS NOT NULL")
    )
    warnings: list[str] = []
    if series and not is_complete_period(last_day, unit):
        dropped = series.pop()
        warnings.append(
            f"The latest {unit} ({as_date(dropped['period']).isoformat()}) is incomplete and "
            "was left out of the growth figures."
        )
    if len(series) < 2:
        ctx.warn(f"Growth needs at least two complete {unit}s of data; skipped.")
        return None
    by_period = {as_date(r["period"]): num(r["revenue"]) for r in series}
    latest = as_date(series[-1]["period"])
    prev_start = shift_period(latest, unit, -1)
    yoy_start = shift_period(latest, unit, -per_year)
    now = by_period[latest]
    pop = pct_change(now, by_period[prev_start]) if prev_start in by_period else None
    yoy = pct_change(now, by_period[yoy_start]) if yoy_start in by_period else None
    if pop is None and yoy is None:
        ctx.warn("No previous period to compare the latest period with; growth skipped.")
        return None
    cur, loc = ctx.currency, ctx.locale
    label = {"month": "month", "quarter": "quarter", "year": "year"}[unit]
    bits = []
    if pop is not None:
        bits.append(
            f"{fmt_pct(pop, loc)} vs the previous {label} "
            f"({fmt_money(by_period[prev_start], cur, loc)})"
        )
    if yoy is not None:
        bits.append(
            f"{fmt_pct(yoy, loc)} vs the same {label} last year "
            f"({fmt_money(by_period[yoy_start], cur, loc)})"
        )
    # a year-over-year change is not distorted by seasonality, so it is the headline when known
    headline = yoy if yoy is not None else pop
    assert headline is not None
    basis = f"vs the same {label} last year" if yoy is not None else f"vs the previous {label}"
    direction = "up" if headline >= 0 else "down"
    reference = by_period[yoy_start] if yoy is not None else by_period[prev_start]
    when = latest.isoformat()[: 7 if unit == "month" else 4]
    keys = [*revenue_key_columns(ctx), ctx.roles.columns["date"]]
    rows_used = sum(int(r["rows"]) for r in series)
    return build_insight(
        ctx,
        module="kpis",
        type="growth",
        title=f"Revenue is {direction} {fmt_pct(abs(headline), loc)} {basis} ({when})",
        summary=(
            f"Net revenue in the latest complete {label} ({latest.isoformat()}) was "
            f"{fmt_money(now, cur, loc)}: " + "; ".join(bits) + "."
        ),
        value=None if headline is None else round(headline, 2),
        sample_size=rows_used,
        method=f"{label}-over-{label} and year-over-year change of net revenue by {label}",
        key_columns=keys,
        money_effect=now - reference,
        payload={
            "period_unit": unit,
            "latest_period": latest.isoformat(),
            "latest_revenue": round(now, 2),
            "period_over_period_pct": None if pop is None else round(pop, 2),
            "year_over_year_pct": None if yoy is None else round(yoy, 2),
            "series": [
                {"period": p.isoformat(), "revenue": round(v, 2)}
                for p, v in sorted(by_period.items())
            ][-36:],
        },
        chart_spec={
            "type": "line",
            "title": f"Net revenue by {label}",
            "x": "period",
            "y": "revenue",
        },
        evidence=[evidence_query(f"Net revenue by {label}", sql, series)],
        excluded=excluded_rows(ctx, keys),
        confidence=series_confidence(len(series), rows_used),
        warnings=tuple(warnings),
        what="figures",
    )
