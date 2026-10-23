"""Per-customer churn features, computed only from purchases on or before a cutoff date.

`features_sql` is the single builder used for the training snapshot, the evaluation snapshot and the
scoring snapshot. Its `purchases` CTE filters `day <= cutoff` before anything is aggregated, so
changing, adding or deleting rows after the cutoff cannot change a feature (tested).
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np

from app.ml.base import AnalysisContext
from app.ml.churn.labels import LOOKBACK_DAYS, flag_expr, future_buyers_cte, purchases_cte
from app.ml.engine import lit

TREND_DAYS = 90
NO_REPEAT_GAP_DAYS = float(LOOKBACK_DAYS)  # average gap of a customer who bought on one day only

# name -> plain words for reasons ("no purchase for ...")
LABELS = {
    "recency_days": "days since the last purchase",
    "frequency": "number of orders",
    "monetary": "total spend",
    "tenure_days": "days since the first purchase",
    "spend_trend": "spend in the last 90 days compared with the 90 days before",
    "avg_gap_days": "average days between purchases",
    "category_count": "number of categories bought",
    "discount_share": "share of spend bought on discount",
}


@dataclass(frozen=True)
class FeatureSet:
    """Which features this dataset supports, in model column order."""

    names: tuple[str, ...]

    @classmethod
    def for_context(cls, ctx: AnalysisContext, *, flag_mode: bool = False) -> "FeatureSet":
        """Pick the features: optional ones need their role; flag mode drops time-sensitive ones."""
        names = ["recency_days", "frequency", "monetary", "tenure_days", "spend_trend"]
        names.append("avg_gap_days")
        if ctx.roles.has("category"):
            names.append("category_count")
        if ctx.roles.has("discount"):
            names.append("discount_share")
        if flag_mode:  # the flag has no date, so these would include behaviour after leaving
            names = [n for n in names if n not in ("recency_days", "spend_trend")]
        return cls(tuple(names))


def _aggregates(ctx: AnalysisContext, cutoff: date) -> str:
    frequency = "count(DISTINCT order_key)" if ctx.roles.has("order_id") else "count(*)"
    recent = lit(cutoff - timedelta(days=TREND_DAYS))
    before = lit(cutoff - timedelta(days=2 * TREND_DAYS))
    return (
        "SELECT customer, "
        f"date_diff('day', max(day), {lit(cutoff)}) AS recency_days, "
        f"{frequency} AS frequency, "
        "sum(revenue) AS monetary, "
        f"date_diff('day', min(day), {lit(cutoff)}) AS tenure_days, "
        f"coalesce(sum(revenue) FILTER (WHERE day > {recent}), 0) AS spend_recent, "
        f"coalesce(sum(revenue) FILTER (WHERE day > {before} AND day <= {recent}), 0) "
        "AS spend_before, "
        "CAST(date_diff('day', min(day), max(day)) AS DOUBLE) "
        "/ nullif(count(DISTINCT day) - 1, 0) AS avg_gap_days, "
        "count(DISTINCT category) AS category_count, "
        "coalesce(sum(revenue) FILTER (WHERE discount > 0), 0) / sum(revenue) AS discount_share "
        "FROM purchases GROUP BY customer"
    )


def features_sql(ctx: AnalysisContext, cutoff: date) -> str:
    """Return SQL: one row per customer active in the year before `cutoff`, as of `cutoff`."""
    return (
        f"WITH {purchases_cte(ctx, cutoff)}, feats AS ({_aggregates(ctx, cutoff)}) "
        f"SELECT * FROM feats WHERE recency_days < {LOOKBACK_DAYS} ORDER BY customer"
    )


def labelled_sql(ctx: AnalysisContext, cutoff: date, window_days: int) -> str:
    """Return SQL: the features at `cutoff` plus `churned` (no purchase in the next W days)."""
    return (
        f"WITH base AS ({features_sql(ctx, cutoff)}), "
        f"{future_buyers_cte(ctx, cutoff, window_days)} "
        "SELECT base.*, CASE WHEN f.customer IS NULL THEN 1 ELSE 0 END AS churned "
        "FROM base LEFT JOIN future f ON base.customer = f.customer ORDER BY base.customer"
    )


def flagged_sql(ctx: AnalysisContext, end: date) -> str:
    """Return SQL for flag mode: features over the whole history plus the churn flag."""
    cust = ctx.roles.q("customer_id")
    return (
        f"WITH {purchases_cte(ctx, end)}, feats AS ({_aggregates(ctx, end)}), "
        f"flags AS (SELECT {cust} AS customer, max({flag_expr(ctx)}) AS churned FROM data "
        f"WHERE {cust} IS NOT NULL GROUP BY {cust}) "
        "SELECT feats.*, flags.churned FROM feats JOIN flags ON feats.customer = flags.customer "
        "WHERE flags.churned IS NOT NULL ORDER BY feats.customer"
    )


def spend_trend(recent: float, before: float) -> float:
    """Log ratio of recent to earlier spend (0 = steady, negative = falling)."""
    return math.log((recent + 1.0) / (before + 1.0))


def matrix(rows: list[dict[str, Any]], features: FeatureSet) -> np.ndarray:
    """Turn feature rows into the model matrix (column order = `features.names`)."""
    out = np.empty((len(rows), len(features.names)), dtype=float)
    for i, r in enumerate(rows):
        values = {
            "recency_days": float(r["recency_days"]),
            "frequency": float(r["frequency"]),
            "monetary": float(r["monetary"]),
            "tenure_days": float(r["tenure_days"]),
            "spend_trend": spend_trend(float(r["spend_recent"]), float(r["spend_before"])),
            "avg_gap_days": (
                NO_REPEAT_GAP_DAYS if r["avg_gap_days"] is None else float(r["avg_gap_days"])
            ),
            "category_count": float(r["category_count"] or 0),
            "discount_share": float(r["discount_share"] or 0.0),
        }
        out[i] = [values[n] for n in features.names]
    return out
