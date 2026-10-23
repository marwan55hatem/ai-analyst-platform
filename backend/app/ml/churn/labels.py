"""Churn label: the window W, the training/evaluation cutoffs and the population at a cutoff.

A customer churned at cutoff c when they bought before c (within `LOOKBACK_DAYS`) but made no
positive purchase in (c, c + W]. W is twice the typical gap between purchases (the median over
customers of each customer's median gap between purchase days), clamped to 30-180 days.
Training uses cutoff c1 = c2 - W and evaluation c2 = end - W, so the
training labels end exactly where the evaluation labels begin (no overlap, no random split in time).
"""

from dataclasses import dataclass
from datetime import date, timedelta

from app.ml.base import AnalysisContext
from app.ml.engine import lit
from app.ml.modules.common import revenue_expr

MIN_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 180
LOOKBACK_DAYS = 365
MIN_HISTORY_DAYS = 180

_TRUE = ("1", "1.0", "true", "t", "yes", "y", "churned", "left", "cancelled", "canceled", "نعم")
_FALSE = ("0", "0.0", "false", "f", "no", "n", "active", "stayed", "retained", "لا")


@dataclass(frozen=True)
class Plan:
    """The label definition of one run: window, cutoffs and the last purchase date."""

    window_days: int
    median_gap_days: float
    end: date
    eval_cutoff: date
    train_cutoff: date

    def history_needed(self) -> int:
        """Days of purchase history required before the training cutoff."""
        return max(MIN_HISTORY_DAYS, 2 * self.window_days)


def choose_window(median_gap: float) -> int:
    """Return W = 2 x the median gap, rounded and clamped to 30-180 days."""
    return int(min(MAX_WINDOW_DAYS, max(MIN_WINDOW_DAYS, round(2 * median_gap))))


def plan_for(end: date, median_gap: float) -> Plan:
    """Build the cutoffs for a dataset whose last positive purchase is on `end`."""
    window = choose_window(median_gap)
    eval_cutoff = end - timedelta(days=window)
    return Plan(window, median_gap, end, eval_cutoff, eval_cutoff - timedelta(days=window))


def purchases_cte(ctx: AnalysisContext, cutoff: date | None = None) -> str:
    """Return `purchases AS (...)`: positive purchases, on or before `cutoff` when given."""
    rev, d, cust = revenue_expr(ctx), ctx.roles.q("date"), ctx.roles.q("customer_id")
    order_key = ctx.roles.q("order_id") if ctx.roles.has("order_id") else "NULL"
    category = ctx.roles.q("category") if ctx.roles.has("category") else "NULL"
    discount = ctx.roles.q("discount") if ctx.roles.has("discount") else "NULL"
    until = f" AND {d} <= {lit(cutoff)}" if cutoff is not None else ""
    return (
        f"purchases AS (SELECT {cust} AS customer, {d} AS day, {order_key} AS order_key, "
        f"CAST({rev} AS DOUBLE) AS revenue, {category} AS category, "
        f"CAST({discount} AS DOUBLE) AS discount FROM data "
        f"WHERE {cust} IS NOT NULL AND {d} IS NOT NULL AND {rev} > 0{until})"
    )


def median_gap_sql(ctx: AnalysisContext) -> str:
    """Return SQL for the typical gap: the median over customers of their own median gap.

    Customer-weighted, so the many gaps of frequent buyers do not set the window alone.
    """
    return (
        f"WITH {purchases_cte(ctx)}, days AS (SELECT DISTINCT customer, day FROM purchases), "
        "gaps AS (SELECT customer, date_diff('day', lag(day) OVER (PARTITION BY customer "
        "ORDER BY day), day) AS gap FROM days), "
        "own AS (SELECT customer, median(gap) AS gap FROM gaps WHERE gap IS NOT NULL "
        "GROUP BY customer) "
        "SELECT median(gap) AS median_gap_days, count(*) AS gaps FROM own"
    )


def future_buyers_cte(ctx: AnalysisContext, cutoff: date, window_days: int) -> str:
    """Return `future AS (...)`: customers with a positive purchase in (cutoff, cutoff + W]."""
    rev, d, cust = revenue_expr(ctx), ctx.roles.q("date"), ctx.roles.q("customer_id")
    until = cutoff + timedelta(days=window_days)
    return (
        f"future AS (SELECT DISTINCT {cust} AS customer FROM data WHERE {cust} IS NOT NULL "
        f"AND {rev} > 0 AND {d} > {lit(cutoff)} AND {d} <= {lit(until)})"
    )


def label_counts_sql(ctx: AnalysisContext, cutoff: date, window_days: int) -> str:
    """Evidence SQL: active customers at a cutoff and how many of them did not buy again in W."""
    since = cutoff - timedelta(days=LOOKBACK_DAYS)
    return (
        f"WITH {purchases_cte(ctx, cutoff)}, "
        f"active AS (SELECT customer FROM purchases GROUP BY customer "
        f"HAVING max(day) > {lit(since)}), {future_buyers_cte(ctx, cutoff, window_days)} "
        f"SELECT {lit(cutoff)} AS cutoff, {window_days} AS window_days, "
        "count(*) AS active_customers, "
        "count(*) FILTER (WHERE f.customer IS NULL) AS churned, "
        "round(100.0 * count(*) FILTER (WHERE f.customer IS NULL) / count(*), 2) AS churn_rate_pct "
        "FROM active a LEFT JOIN future f ON a.customer = f.customer"
    )


def flag_expr(ctx: AnalysisContext) -> str:
    """Return SQL turning the churn_flag text into 1 / 0 / NULL (unknown values: NULL)."""
    flag = f"lower(trim({ctx.roles.q('churn_flag')}))"
    yes = ", ".join(f"'{v}'" for v in _TRUE)
    no = ", ".join(f"'{v}'" for v in _FALSE)
    return f"CASE WHEN {flag} IN ({yes}) THEN 1 WHEN {flag} IN ({no}) THEN 0 END"
