"""RFM scoring in SQL: per-customer recency, frequency and spend, quintile scores and segments.

Scores are 1 + floor(5 * (RANK - 1) / n): customers with the same value share a score (every
one-time buyer gets F=1), and the most recent buyers get R=5. The segment comes from the R and F
scores through the explicit table below, which covers all 25 cells.
"""

from datetime import date

from app.ml.base import AnalysisContext
from app.ml.engine import lit
from app.ml.modules.common import revenue_expr

SEGMENTS = (
    "Champions",
    "Loyal",
    "Potential loyalists",
    "New",
    "At risk",
    "Hibernating",
    "Lost",
)

# (R score, F score) -> segment. Rows: R 5..1 (5 = bought most recently); columns: F 1..5.
_GRID = {
    5: ("New", "Potential loyalists", "Potential loyalists", "Champions", "Champions"),
    4: ("New", "Potential loyalists", "Potential loyalists", "Champions", "Champions"),
    3: ("Hibernating", "Hibernating", "Loyal", "Loyal", "Loyal"),
    2: ("Hibernating", "Hibernating", "At risk", "At risk", "At risk"),
    1: ("Lost", "Lost", "At risk", "At risk", "At risk"),
}
SEGMENT_MAP: dict[tuple[int, int], str] = {
    (r, f): name for r, names in _GRID.items() for f, name in enumerate(names, start=1)
}

# Rule-based recommended action per segment; always shown as a suggestion, never as advice.
ACTIONS: dict[str, str] = {
    "Champions": "Reward them: early access, loyalty perks, and ask for reviews and referrals.",
    "Loyal": "Upsell higher-value products and invite them to a loyalty programme.",
    "Potential loyalists": "Give a second-purchase incentive or membership to build the habit.",
    "New": "Onboard well: a welcome series and a reason to buy a second time soon.",
    "At risk": "Win-back offer: a personal reminder or discount before they lapse for good.",
    "Hibernating": "Low-cost reactivation with seasonal offers; do not over-invest.",
    "Lost": "Try one win-back campaign; otherwise stop spending marketing money on them.",
}


def suggestion(segment: str) -> dict[str, str]:
    """Return the recommended action of a segment, marked as a suggestion."""
    return {"text": f"Suggestion: {ACTIONS[segment]}", "kind": "suggestion"}


def segment_case(r: str = "r_score", f: str = "f_score") -> str:
    """Return a SQL CASE expression implementing SEGMENT_MAP."""
    whens = " ".join(
        f"WHEN {r} = {rs} AND {f} = {fs} THEN '{name}'"
        for (rs, fs), name in sorted(SEGMENT_MAP.items(), reverse=True)
    )
    return f"CASE {whens} END"


def _score(column: str, direction: str) -> str:
    return (
        f"CAST(1 + floor(5 * (rank() OVER (ORDER BY {column} {direction}) - 1) "
        f"/ count(*) OVER ()) AS INTEGER)"
    )


def customers_cte(ctx: AnalysisContext, end: date) -> str:
    """Return the WITH clause that defines `segmented`: one scored row per customer."""
    rev, d, cust = revenue_expr(ctx), ctx.roles.q("date"), ctx.roles.q("customer_id")
    has_orders = ctx.roles.has("order_id")
    order_key = ctx.roles.q("order_id") if has_orders else "NULL"
    frequency = "count(DISTINCT order_key)" if has_orders else "count(*)"
    return (
        f"WITH purchases AS (SELECT {cust} AS customer, {d} AS day, {order_key} AS order_key, "
        f"{rev} AS revenue FROM data "
        f"WHERE {cust} IS NOT NULL AND {d} IS NOT NULL AND {rev} > 0), "
        f"customers AS (SELECT customer, "
        f"date_diff('day', max(day), {lit(end)}) AS recency_days, "
        f"{frequency} AS frequency, sum(revenue) AS monetary, "
        f"date_diff('day', min(day), {lit(end)}) AS tenure_days "
        f"FROM purchases GROUP BY customer), "
        f"scored AS (SELECT *, {_score('recency_days', 'DESC')} AS r_score, "
        f"{_score('frequency', 'ASC')} AS f_score, {_score('monetary', 'ASC')} AS m_score "
        f"FROM customers), "
        f"segmented AS (SELECT *, {segment_case()} AS segment FROM scored)"
    )


def summary_sql(ctx: AnalysisContext, end: date) -> str:
    """Return the evidence query: one row per segment, no customer ids."""
    return (
        f"{customers_cte(ctx, end)} "
        f"SELECT segment, count(*) AS customers, "
        f"round(100.0 * sum(monetary) / sum(sum(monetary)) OVER (), 2) AS revenue_share_pct, "
        f"round(sum(monetary), 2) AS revenue, "
        f"round(avg(recency_days), 1) AS avg_recency_days, "
        f"round(avg(frequency), 2) AS avg_orders, round(avg(monetary), 2) AS avg_spend, "
        f"round(sum(monetary * 365.0 / greatest(tenure_days, 365)), 2) AS yearly_spend "
        f"FROM segmented GROUP BY segment ORDER BY revenue DESC, segment"
    )


def features_sql(ctx: AnalysisContext, end: date) -> str:
    """Return the evidence query that describes the clustering inputs (aggregates only)."""
    cols = []
    for name in ("recency_days", "frequency", "monetary", "aov", "tenure_days"):
        cols.append(
            f"round(avg({name}), 2) AS avg_{name}, "
            f"round(quantile_cont({name}, 0.5), 2) AS median_{name}"
        )
    return (
        f"{customers_cte(ctx, end)}, features AS (SELECT *, monetary / frequency AS aov "
        f"FROM segmented) SELECT count(*) AS customers, {', '.join(cols)} FROM features"
    )
