"""Cohort retention: monthly acquisition cohorts and the share of customers who come back."""

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from app.ml.base import AnalysisContext, AnalysisModule, Insight
from app.ml.engine import lit, num
from app.ml.format import fmt_money, fmt_number, fmt_pct
from app.ml.modules.common import (
    as_date,
    build_insight,
    evidence_query,
    excluded_rows,
    is_complete_period,
    revenue_key_columns,
    shift_period,
)

MAX_OFFSET = 12
MIN_CUSTOMERS_FOR_CONFIDENCE = 30


class CohortRetentionModule(AnalysisModule):
    """Group customers by first purchase month and track how many return."""

    name = "cohort_retention"
    title = "Cohort retention"
    requires = frozenset({"customer_id", "date"})

    def run(self, context: AnalysisContext) -> list[Insight]:
        """Return the retention matrix insight, or nothing when the history is too short."""
        ctx = context
        cust, d = ctx.roles.q("customer_id"), ctx.roles.q("date")
        rev = ctx.roles.revenue_expr()
        positive = f" AND {rev} > 0" if rev is not None else ""
        last = ctx.db.scalar(
            f"SELECT max({d}) FROM data WHERE {cust} IS NOT NULL AND {d} IS NOT NULL"
        )
        if last is None:
            ctx.warn("No row has both a customer and a date; cohort analysis was skipped.")
            return []
        last_day = as_date(last)
        warnings: list[str] = []
        if is_complete_period(last_day, "month"):
            end = last_day + timedelta(days=1)
        else:
            end = last_day.replace(day=1)
            warnings.append(
                f"The incomplete month {end.isoformat()[:7]} was left out so it does not "
                "understate retention."
            )
        sql = (
            f"WITH purchases AS (SELECT {cust} AS customer, "
            f"CAST(date_trunc('month', {d}) AS DATE) AS month FROM data "
            f"WHERE {cust} IS NOT NULL AND {d} IS NOT NULL AND {d} < {lit(end)}{positive} "
            f"GROUP BY 1, 2), "
            f"firsts AS (SELECT customer, min(month) AS cohort FROM purchases GROUP BY customer) "
            f"SELECT f.cohort AS cohort, date_diff('month', f.cohort, p.month) AS month_offset, "
            f"count(DISTINCT p.customer) AS customers FROM purchases AS p "
            f"JOIN firsts AS f ON p.customer = f.customer GROUP BY 1, 2 ORDER BY 1, 2"
        )
        rows = ctx.db.rows(sql)
        if not rows:
            ctx.warn("No purchases found for cohort analysis.")
            return []
        found = self._insight(ctx, rows, sql, end, warnings, rev)
        return [] if found is None else [found]

    def _insight(
        self,
        ctx: AnalysisContext,
        rows: list[dict[str, Any]],
        sql: str,
        end: date,
        warnings: list[str],
        rev: str | None,
    ) -> Insight | None:
        cells: dict[date, dict[int, int]] = defaultdict(dict)
        for r in rows:
            cells[as_date(r["cohort"])][int(r["month_offset"])] = int(r["customers"])
        sizes = {c: offsets.get(0, 0) for c, offsets in cells.items()}
        last_month = shift_period(end, "month", -1)
        cohorts = sorted(cells)
        matrix: list[dict[str, Any]] = []
        for c in cohorts:
            observable = _months_between(c, last_month)
            matrix.append(
                {
                    "cohort": c.isoformat(),
                    "size": sizes[c],
                    "retention": [
                        round(100.0 * cells[c].get(k, 0) / sizes[c], 2) if k <= observable else None
                        for k in range(MAX_OFFSET + 1)
                    ],
                }
            )
        customers = sum(sizes.values())
        r1 = _weighted(cells, sizes, cohorts, last_month, 1)
        r3 = _weighted(cells, sizes, cohorts, last_month, 3)
        if r1 is None:
            ctx.warn("Cohort retention needs at least two months of purchases; skipped.")
            return None
        cur, loc = ctx.currency, ctx.locale
        eligible = sum(sizes[c] for c in cohorts if _months_between(c, last_month) >= 1)
        arpu = _revenue_per_active_month(ctx, rows, end, rev)
        lost = eligible * (1 - r1 / 100.0)
        negatives = (
            int(ctx.db.scalar(f"SELECT count(*) FROM data WHERE {rev} < 0") or 0) if rev else 0
        )
        if negatives:
            warnings.append(
                f"{fmt_number(negatives, loc)} return/refund rows were not counted as purchases."
            )
        best = max(
            (m for m in matrix if m["retention"][1] is not None and m["size"] >= 5),
            key=lambda m: (m["retention"][1], m["cohort"]),
            default=None,
        )
        text = (
            f"{fmt_pct(r1, loc)} of customers buy again in the month after their first purchase"
            + (f" and {fmt_pct(r3, loc)} are still buying 3 months later" if r3 is not None else "")
            + f" ({fmt_number(customers, loc)} customers in {len(cohorts)} monthly cohorts). "
            f"About {fmt_number(round(lost), loc)} customers did not return the next month, "
            f"worth roughly {fmt_money(lost * arpu, cur, loc)} of monthly revenue."
        )
        keys = [ctx.roles.columns["customer_id"], ctx.roles.columns["date"]]
        keys += revenue_key_columns(ctx) if rev else []
        confidence = None if customers >= MIN_CUSTOMERS_FOR_CONFIDENCE else "low"
        return build_insight(
            ctx,
            module="cohort_retention",
            type="cohort_retention",
            title=f"{fmt_pct(r1, loc)} of customers return the next month",
            summary=text,
            value=round(r1, 2),
            sample_size=customers,
            method=(
                "monthly cohorts by first purchase month; a customer is retained in a month when "
                "they have a positive purchase (refunds do not count)"
            ),
            key_columns=keys,
            money_effect=lost * arpu,
            payload={
                "month1_retention_pct": round(r1, 2),
                "month3_retention_pct": None if r3 is None else round(r3, 2),
                "customers": customers,
                "cohort_count": len(cohorts),
                "best_cohort": None if best is None else best["cohort"],
                "last_complete_month": last_month.isoformat(),
                "matrix": matrix,
            },
            chart_spec={
                "type": "heatmap",
                "title": "Retention by cohort (% of the cohort still buying)",
                "x": "months since first purchase",
                "y": "cohort",
            },
            evidence=[evidence_query("Customers per cohort and month offset", sql, rows)],
            excluded=excluded_rows(ctx, keys),
            confidence=confidence,  # type: ignore[arg-type]
            warnings=tuple(warnings),
            what="figures",
        )


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


def _weighted(
    cells: dict[date, dict[int, int]],
    sizes: dict[date, int],
    cohorts: list[date],
    last_month: date,
    offset: int,
) -> float | None:
    """Customer-weighted retention at an offset over cohorts old enough to be observed."""
    eligible = [c for c in cohorts if _months_between(c, last_month) >= offset and sizes[c] > 0]
    total = sum(sizes[c] for c in eligible)
    if not eligible or total == 0:
        return None
    return 100.0 * sum(cells[c].get(offset, 0) for c in eligible) / total


def _revenue_per_active_month(
    ctx: AnalysisContext, rows: list[dict[str, Any]], end: date, rev: str | None
) -> float:
    """Average positive revenue per customer who was active in a month."""
    active_months = sum(int(r["customers"]) for r in rows)
    if rev is None or active_months == 0:
        return 0.0
    cust, d = ctx.roles.q("customer_id"), ctx.roles.q("date")
    total = num(
        ctx.db.scalar(
            f"SELECT sum({rev}) FROM data WHERE {cust} IS NOT NULL AND {d} IS NOT NULL "
            f"AND {d} < {lit(end)} AND {rev} > 0"
        )
    )
    return total / active_months
