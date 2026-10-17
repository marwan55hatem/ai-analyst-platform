"""Pareto / ABC analysis of products, categories, regions and channels, plus dead stock."""

from datetime import timedelta
from typing import Any

from app.ml.base import AnalysisContext, AnalysisModule, Insight
from app.ml.engine import lit, num
from app.ml.format import fmt_money, fmt_number, fmt_pct
from app.ml.modules.common import (
    SENSITIVITY,
    as_date,
    build_insight,
    evidence_query,
    excluded_rows,
    revenue_expr,
    revenue_key_columns,
    revenue_note,
)
from app.ml.roles import DIMENSION_ROLES

A_SHARE = 0.80
B_SHARE = 0.95
TOP_ROWS = 20
MIN_ITEMS_FOR_ABC = 5
DEFAULT_DEAD_DAYS = 90
MAX_DEAD_ROWS = 200
LABELS = {
    "product": "products",
    "category": "categories",
    "region": "regions",
    "channel": "channels",
}


class ParetoModule(AnalysisModule):
    """Who earns the money: concentration by dimension, the long tail and dead stock."""

    name = "pareto"
    title = "Top contributors and dead stock"
    requires = frozenset({"revenue"})

    def run(self, context: AnalysisContext) -> list[Insight]:
        """Return one Pareto insight per available dimension and a dead stock insight."""
        dims = [r for r in DIMENSION_ROLES if context.roles.has(r)]
        if not dims:
            context.warn("No product, category, region or channel column was found.")
            return []
        insights: list[Insight] = []
        for role in dims:
            found = _pareto(context, role)
            if found is not None:
                insights.append(found)
        if context.roles.has("product"):
            if context.roles.has("date"):
                dead = _dead_stock(context)
                if dead is not None:
                    insights.append(dead)
            else:
                context.warn("Dead stock needs a date column and was skipped.")
        return insights

    def notes(self, roles: Any) -> list[str]:
        """Explain which parts will be skipped for lack of columns."""
        notes = []
        if not roles.has("product"):
            notes.append("no product column, so dead stock is skipped")
        elif not roles.has("date"):
            notes.append("no date column, so dead stock is skipped")
        return notes


def _pareto(ctx: AnalysisContext, role: str) -> Insight | None:
    rev, dim = revenue_expr(ctx), ctx.roles.q(role)
    sql = (
        f"SELECT {dim} AS item, sum({rev}) AS revenue, count(*) AS rows FROM data "
        f"WHERE {rev} > 0 AND {dim} IS NOT NULL GROUP BY {dim} ORDER BY revenue DESC, item"
    )
    rows = ctx.db.rows(sql)
    label = LABELS[role]
    if len(rows) < 2:
        ctx.warn(f"Only one {role} value found, so there is nothing to rank; skipped.")
        return None
    returns = {
        str(r["item"]): num(r["returns"])
        for r in ctx.db.rows(
            f"SELECT {dim} AS item, sum(-{rev}) AS returns FROM data "
            f"WHERE {rev} < 0 AND {dim} IS NOT NULL GROUP BY {dim}"
        )
    }
    total = sum(num(r["revenue"]) for r in rows)
    cumulative = 0.0
    items: list[dict[str, Any]] = []
    for r in rows:
        revenue = num(r["revenue"])
        before = cumulative / total
        cumulative += revenue
        abc = "A" if before < A_SHARE else "B" if before < B_SHARE else "C"
        items.append(
            {
                "item": str(r["item"]),
                "revenue": round(revenue, 2),
                "share_pct": round(100.0 * revenue / total, 2),
                "cumulative_pct": round(100.0 * cumulative / total, 2),
                "abc": abc,
                "returns": round(returns.get(str(r["item"]), 0.0), 2),
            }
        )
    counts = {k: sum(1 for i in items if i["abc"] == k) for k in "ABC"}
    tail = [i for i in items if i["abc"] != "A"]
    tail_revenue_pct = sum(i["share_pct"] for i in tail)
    top = items[0]
    cur, loc = ctx.currency, ctx.locale
    keys = [*revenue_key_columns(ctx), ctx.roles.columns[role]]
    negatives = int(ctx.db.scalar(f"SELECT count(*) FROM data WHERE {rev} < 0") or 0)
    warnings = []
    if negatives:
        warnings.append(
            f"{fmt_number(negatives, loc)} return/refund rows were left out; shares use positive "
            "sales only."
        )
    note = revenue_note(ctx)
    if note:
        warnings.append(note)
    sample = sum(int(r["rows"]) for r in rows)
    return build_insight(
        ctx,
        module="pareto",
        type="pareto",
        title=f"Top {role}: {top['item']} ({fmt_pct(top['share_pct'], loc)} of sales)",
        summary=(
            f"{top['item']} is the top {role} with {fmt_money(top['revenue'], cur, loc)} "
            f"({fmt_pct(top['share_pct'], loc)} of positive sales)."
            + (
                f" {counts['A']} of {len(items)} {label} (class A) produce "
                f"{int(A_SHARE * 100)}% of sales; the long tail of {len(tail)} "
                f"{label if len(tail) != 1 else role} (classes B and C) adds "
                f"{fmt_pct(tail_revenue_pct, loc)}."
                if len(items) >= MIN_ITEMS_FOR_ABC
                else f" There are only {len(items)} {label}, so no ABC split is shown."
            )
        ),
        value=top["item"],
        sample_size=sample,
        method="ABC / Pareto on positive sales: A to 80% cumulative share, B to 95%, C the rest",
        key_columns=keys,
        money_effect=top["revenue"] * SENSITIVITY,
        payload={
            "dimension": role,
            "revenue_basis": "positive sales only",
            "total_revenue": round(total, 2),
            "item_count": len(items),
            "top_item": top["item"],
            "top_share_pct": top["share_pct"],
            "abc_counts": counts,
            "long_tail_items": len(tail),
            "long_tail_share_of_items_pct": round(100.0 * len(tail) / len(items), 2),
            "long_tail_revenue_pct": round(tail_revenue_pct, 2),
            "items": items[:TOP_ROWS],
        },
        chart_spec={
            "type": "bar",
            "title": f"Top {label} by sales",
            "data": [{"label": i["item"], "value": i["revenue"]} for i in items[:10]],
        },
        evidence=[evidence_query(f"Positive sales by {role}", sql, rows)],
        excluded=excluded_rows(ctx, keys),
        warnings=tuple(warnings),
        what="totals",
    )


def _dead_stock(ctx: AnalysisContext) -> Insight | None:
    days = int(ctx.config.get("dead_stock_days") or DEFAULT_DEAD_DAYS)
    rev, d, prod = revenue_expr(ctx), ctx.roles.q("date"), ctx.roles.q("product")
    bounds = ctx.db.one(
        f"SELECT min({d}) AS first, max({d}) AS last FROM data WHERE {d} IS NOT NULL"
    )
    if bounds["last"] is None:
        return None
    first, last = as_date(bounds["first"]), as_date(bounds["last"])
    if (last - first).days <= days:
        ctx.warn(f"The history is no longer than {days} days, so dead stock was skipped.")
        return None
    cutoff = last - timedelta(days=days)
    xrev, xd, xprod = (
        ctx.roles.revenue_expr("x"),
        ctx.roles.q("date", "x"),
        ctx.roles.q("product", "x"),
    )
    sql = (
        f"WITH quiet AS (SELECT {prod} AS item, max({d}) AS last_sale FROM data "
        f"WHERE {rev} > 0 AND {d} IS NOT NULL AND {prod} IS NOT NULL GROUP BY {prod} "
        f"HAVING max({d}) < {lit(cutoff)}) "
        f"SELECT q.item AS item, q.last_sale AS last_sale, sum({xrev}) AS lifetime_revenue, "
        f"sum(CASE WHEN {xd} > q.last_sale - {days} THEN {xrev} END) AS run_rate "
        f"FROM data AS x JOIN quiet AS q ON {xprod} = q.item "
        f"WHERE {xrev} > 0 AND {xd} IS NOT NULL "
        f"GROUP BY q.item, q.last_sale ORDER BY lifetime_revenue DESC, item"
    )
    rows = ctx.db.rows(sql)
    if not rows:
        return None
    total_items = int(
        ctx.db.scalar(f"SELECT count(DISTINCT {prod}) FROM data WHERE {rev} > 0") or 0
    )
    sale_rows = int(
        ctx.db.scalar(
            f"SELECT count(*) FROM data WHERE {rev} > 0 AND {d} IS NOT NULL AND {prod} IS NOT NULL"
        )
        or 0
    )
    cur, loc = ctx.currency, ctx.locale
    run_rate = sum(num(r["run_rate"]) for r in rows)
    names = [str(r["item"]) for r in rows]
    shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    keys = [*revenue_key_columns(ctx), ctx.roles.columns["date"], ctx.roles.columns["product"]]
    return build_insight(
        ctx,
        module="pareto",
        type="dead_stock",
        title=f"{len(rows)} products with no sales in the last {days} days",
        summary=(
            f"{len(rows)} of {total_items} products have not sold since "
            f"{cutoff.isoformat()} ({days} days before the dataset's last date, "
            f"{last.isoformat()}): {shown}. They earned {fmt_money(run_rate, cur, loc)} in the "
            f"{days} days before they went quiet."
        ),
        value=names,
        sample_size=sale_rows,
        method=f"products whose last sale is more than {days} days before the dataset's last date",
        key_columns=keys,
        money_effect=run_rate,
        payload={
            "days": days,
            "cutoff": cutoff,
            "as_of": last,
            "dead_count": len(rows),
            "product_count": total_items,
            "item_names": names[:MAX_DEAD_ROWS],
            "items": [
                {
                    "item": str(r["item"]),
                    "last_sale": as_date(r["last_sale"]),
                    "lifetime_revenue": round(num(r["lifetime_revenue"]), 2),
                    "run_rate": round(num(r["run_rate"]), 2),
                }
                for r in rows[:MAX_DEAD_ROWS]
            ],
        },
        chart_spec={},
        evidence=[evidence_query("Products with no recent sales", sql, rows)],
        excluded=excluded_rows(ctx, keys),
        what="figures",
    )
