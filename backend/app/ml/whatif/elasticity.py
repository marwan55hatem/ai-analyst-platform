"""Price elasticity from a log-log regression with controls, per product, category or all items.

Observations are (item, week) totals: units sold and a price index = the average of
log(price / the product's median price), so products of different price levels can be pooled. The
regression is

    log(units) = e x log(price index) + item effects + month effects + trend [+ b x discount]

by OLS with a 95% confidence interval. Prices move per period for every item of a group, so items of
the same period share its shocks: standard errors are clustered by period (HC1 when each period has
one row). Weeks
with no sales cannot be logged and are dropped (and counted). An item group with too few periods or
too little price variation is skipped; an estimate that is positive, larger than 5 in size, or whose
interval spans zero is marked unreliable and is never used for a point forecast.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import statsmodels.api as sm

from app.ml.anomaly.rows import discount_rate_expr
from app.ml.base import AnalysisContext
from app.ml.modules.common import revenue_expr

MIN_PERIODS = 20
MIN_PRICE_SD = 0.02  # standard deviation of the log price index (about a 2% CV)
MIN_PRICE_LEVELS = 3
MAX_ABS_ELASTICITY = 5.0
UNRELIABLE_TEXT = "price changes in this data are not clearly linked to sales"


@dataclass
class Estimate:
    """One elasticity estimate (or why there is none) for a group of items."""

    scope: str  # product | category | all
    name: str
    status: str  # ok | unreliable | skipped
    reason: str | None = None
    elasticity: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    discount_effect: float | None = None  # change in log units per +1.0 of discount rate
    discount_ci: tuple[float, float] | None = None
    periods: int = 0
    items: int = 0
    items_with_price_changes: int = 0
    grain: str = "week"
    price_range_pct: tuple[float, float] | None = None  # observed p5..p95 of price vs usual
    discount_range: tuple[float, float] | None = None

    def usable(self) -> bool:
        """Only a reliable estimate may produce a revenue range."""
        return self.status == "ok"

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe summary."""
        return {
            "scope": self.scope,
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "elasticity": None if self.elasticity is None else round(self.elasticity, 4),
            "ci95": (
                None
                if self.ci_low is None or self.ci_high is None
                else [round(self.ci_low, 4), round(self.ci_high, 4)]
            ),
            "periods": self.periods,
            "items": self.items,
            "items_with_price_changes": self.items_with_price_changes,
            "discount_effect": (
                None if self.discount_effect is None else round(self.discount_effect, 4)
            ),
            "discount_ci95": (
                None if self.discount_ci is None else [round(v, 4) for v in self.discount_ci]
            ),
            "grain": self.grain,
            "observed_price_range_pct": (
                None
                if self.price_range_pct is None
                else [round(v, 2) for v in self.price_range_pct]
            ),
        }


def available(ctx: AnalysisContext) -> str | None:
    """Return why elasticity cannot be estimated at all, or None."""
    if not ctx.roles.has("quantity"):
        return "there is no quantity column, so units sold cannot be measured"
    if not ctx.roles.has("date"):
        return "there is no date column"
    if not (ctx.roles.has("price") or ctx.roles.has("revenue")):
        return "there is no price column"
    return None


def _price(ctx: AnalysisContext) -> str:
    if ctx.roles.has("price"):
        return f"CAST({ctx.roles.q('price')} AS DOUBLE)"
    qty = ctx.roles.q("quantity")
    return f"CAST({revenue_expr(ctx)} AS DOUBLE) / nullif(CAST({qty} AS DOUBLE), 0)"


def periods_sql(ctx: AnalysisContext, item_role: str | None, grain: str, where: str = "") -> str:
    """Return SQL: units and the log price index per item and period (positive sales).

    `item_role` None pools every line as one item (a file with no product or category).
    """
    d, qty, rev = ctx.roles.q("date"), ctx.roles.q("quantity"), revenue_expr(ctx)
    item = ctx.roles.q(item_role) if item_role else "'all'"
    product = ctx.roles.q("product") if ctx.roles.has("product") else item
    rate = discount_rate_expr(ctx)
    disc = f"CAST({rate} AS DOUBLE)" if rate else "0.0"
    filters = f" AND {where}" if where else ""
    return (
        f"WITH lines AS (SELECT {item} AS item, {product} AS product, "
        f"date_trunc('{grain}', {d}) AS period, CAST({qty} AS DOUBLE) AS units, "
        f"{_price(ctx)} AS price, coalesce({disc}, 0) AS discount FROM data "
        f"WHERE {d} IS NOT NULL AND {qty} > 0 AND {rev} > 0 AND {item} IS NOT NULL{filters}), "
        "weekly AS (SELECT product, period, avg(price) AS price FROM lines WHERE price > 0 "
        "GROUP BY product, period), "
        "usual AS (SELECT product, median(price) AS usual_price FROM weekly GROUP BY product) "
        "SELECT item, period, month(period) AS month, sum(units) AS units, "
        "round(avg(ln(price / usual_price)), 6) AS log_price_index, "
        "round(sum(discount * units) / sum(units), 6) AS discount_rate, count(*) AS lines "
        "FROM lines JOIN usual USING (product) WHERE price > 0 "
        "GROUP BY item, period ORDER BY item, period"
    )


def _design(rows: list[dict[str, Any]], with_discount: bool) -> tuple[np.ndarray, np.ndarray, int]:
    """Return (X, y, index of the discount column or -1); price is always column 0."""
    items = sorted({str(r["item"]) for r in rows})
    first = min(r["period"] for r in rows)
    cols: list[list[float]] = []
    for r in rows:
        trend = (r["period"] - first).days / 365.25
        row = [float(r["log_price_index"]), 1.0, trend]
        row += [1.0 if str(r["item"]) == i else 0.0 for i in items[1:]]
        row += [1.0 if int(r["month"]) == m else 0.0 for m in range(2, 13)]
        if with_discount:
            row.append(float(r["discount_rate"]))
        cols.append(row)
    x = np.array(cols, dtype=float)
    keep = x.std(axis=0) > 0  # drop months never seen and constant controls
    keep[0] = keep[1] = True
    discount_index = -1
    if with_discount and keep[-1]:
        discount_index = int(keep.sum()) - 1
    y = np.log(np.array([float(r["units"]) for r in rows]))
    return x[:, keep], y, discount_index


def estimate(
    rows: list[dict[str, Any]], scope: str, name: str, grain: str, with_discount: bool
) -> Estimate:
    """Fit one group of (item, period) rows and apply the skip and sanity rules."""
    est = Estimate(scope=scope, name=name, status="skipped", grain=grain)
    est.items = len({str(r["item"]) for r in rows})
    by_item: dict[str, list[float]] = {}
    for r in rows:
        by_item.setdefault(str(r["item"]), []).append(float(r["log_price_index"]))
    est.items_with_price_changes = sum(
        1 for v in by_item.values() if len(v) > 1 and float(np.std(v)) >= MIN_PRICE_SD
    )
    est.periods = len(rows)
    if len(rows) < MIN_PERIODS:
        est.reason = f"only {len(rows)} periods with sales; at least {MIN_PERIODS} are needed"
        return est
    log_price = np.array([float(r["log_price_index"]) for r in rows])
    if (
        float(log_price.std()) < MIN_PRICE_SD
        or len(np.unique(np.round(log_price, 2))) < MIN_PRICE_LEVELS
    ):
        est.reason = "prices hardly changed, so their effect on sales cannot be measured"
        return est
    x, y, discount_index = _design(rows, with_discount)
    if x.shape[0] <= x.shape[1] + 5:
        est.reason = "too few periods for the number of controls"
        return est
    periods = sorted({r["period"] for r in rows})
    if len(periods) < len(rows):  # several items share a period: their errors share its shock
        index = {p: i for i, p in enumerate(periods)}
        groups = np.array([index[r["period"]] for r in rows])
        fit = sm.OLS(y, x).fit(cov_type="cluster", cov_kwds={"groups": groups})
    else:
        fit = sm.OLS(y, x).fit(cov_type="HC1")
    params, ci = np.asarray(fit.params), np.asarray(fit.conf_int(0.05))
    est.elasticity, est.ci_low, est.ci_high = float(params[0]), float(ci[0, 0]), float(ci[0, 1])
    if discount_index >= 0:
        est.discount_effect = float(params[discount_index])
        est.discount_ci = (float(ci[discount_index, 0]), float(ci[discount_index, 1]))
        rates = np.array([float(r["discount_rate"]) for r in rows])
        est.discount_range = (float(np.quantile(rates, 0.05)), float(np.quantile(rates, 0.95)))
    est.price_range_pct = (
        100.0 * (float(np.exp(np.quantile(log_price, 0.05))) - 1.0),
        100.0 * (float(np.exp(np.quantile(log_price, 0.95))) - 1.0),
    )
    est.status, est.reason = sanity(est.elasticity, est.ci_low, est.ci_high)
    return est


def sanity(value: float, low: float, high: float) -> tuple[str, str | None]:
    """Mark an estimate unreliable when positive, above 5 in size or not clearly non-zero."""
    if value > 0:
        return (
            "unreliable",
            f"{UNRELIABLE_TEXT} (the estimate is positive: higher prices with higher sales)",
        )
    if abs(value) > MAX_ABS_ELASTICITY:
        return "unreliable", f"{UNRELIABLE_TEXT} (the estimate of {value:.1f} is implausibly large)"
    if low <= 0 <= high:
        return "unreliable", f"{UNRELIABLE_TEXT} (the 95% interval includes zero)"
    return "ok", None


def fit_group(
    ctx: AnalysisContext, item_role: str | None, scope: str, name: str, where: str
) -> tuple[Estimate, str, list[dict[str, Any]]]:
    """Estimate one group weekly (monthly when weeks are too few); return it, its SQL and rows."""
    with_discount = ctx.roles.has("discount")
    est: Estimate | None = None
    sql = ""
    rows: list[dict[str, Any]] = []
    for grain in ("week", "month"):
        sql = periods_sql(ctx, item_role, grain, where)
        rows = ctx.db.rows(sql)
        est = estimate(rows, scope, name, grain, with_discount)
        if est.status != "skipped" or len(rows) >= MIN_PERIODS:
            break
    assert est is not None
    return est, sql, rows
