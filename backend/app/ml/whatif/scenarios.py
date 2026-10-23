"""What-if scenarios: the yearly effect of changing one lever, always as a range with assumptions.

Baseline = the last 12 complete months of the chosen scope (fewer months are scaled to a year and
lower the confidence). Its own uncertainty is +/- 1.96 x the standard error of a month
(sd / mean / sqrt(months)), "normal month-to-month variation". Then per lever:

* price  +c: units x (1+c)^e, revenue x (1+c)^(1+e); e and its 95% interval from `elasticity`
  (product, else its category, else all items; unreliable estimates are never used).
* discount +c (relative change of the average discount rate r): units x exp(b x r x c), net price x
  (1 - r(1+c)) / (1 - r); b from the same regression.
* volume +c: units +c at today's prices; the range covers the extra units coming from the cheapest
  to the dearest group (category, else product).
* cost +c: unit cost +c, revenue unchanged.

Profit change needs a cost column (unit cost is assumed constant per unit). A change outside what
the data has seen carries an "extrapolation risk" warning.
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np

from app.core.errors import UnprocessableError
from app.ml.anomaly.rows import discount_rate_expr
from app.ml.base import AnalysisContext
from app.ml.engine import lit, num
from app.ml.format import fmt_number, fmt_pct
from app.ml.modules.common import as_date, evidence_query, revenue_expr, shift_period
from app.ml.whatif.elasticity import UNRELIABLE_TEXT, Estimate, available, fit_group
from app.services.profiling.parsing import quote_literal

MAX_CHANGE_PCT = 30.0
MAX_DISCOUNT_RATE = 0.95
METHODS = {
    "price": "units x (1+c)^e, revenue x (1+c)^(1+e); e from a log-log regression with controls "
    "(clustered 95% interval); range = interval ends x the baseline band",
    "discount": "units x exp(b x rate x c), net price x (1 - rate(1+c)) / (1 - rate); b from the "
    "same regression; range = interval ends x the baseline band",
    "volume": "revenue x (1+c) at today's prices; range = extra units from the cheapest to the "
    "dearest group x the baseline band",
    "cost": "profit - c x cost, revenue unchanged; range = the baseline band of cost",
}
BASELINE_MONTHS = 12
MIN_BASELINE_MONTHS = 3
Z95 = 1.96


@dataclass(frozen=True)
class Scenario:
    """One requested change."""

    lever: str
    change_pct: float
    scope: str = "all"
    target: str | None = None


@dataclass
class Baseline:
    """The last complete months of the scope, scaled to a year."""

    start: date
    end: date
    months: int
    revenue: float
    units: float | None
    cost: float | None
    discount_rate: float | None
    revenue_h: float  # relative half-width of normal variation
    cost_h: float
    monthly_units: list[float]
    monthly_unit_cost: list[float]
    evidence: dict[str, Any]


def _scope_filter(ctx: AnalysisContext, scope: str, target: str | None) -> str:
    if scope == "all":
        return ""
    if scope not in ("category", "product") or not ctx.roles.has(scope):
        raise UnprocessableError(
            f"This dataset has no {scope} column, so scope '{scope}' cannot be used.",
            code="invalid_scope",
        )
    if not target:
        raise UnprocessableError(
            f"Scope '{scope}' needs a target ({scope} name).", code="invalid_scope"
        )
    column = ctx.roles.q(scope)
    found = ctx.db.scalar(f"SELECT count(*) FROM data WHERE {column} = {quote_literal(target)}")
    if not found:
        raise UnprocessableError(
            f"No {scope} named '{target}' in this dataset.", code="unknown_target"
        )
    return f"{column} = {quote_literal(target)}"


def _month_window(ctx: AnalysisContext, where: str) -> tuple[date, date, int]:
    d = ctx.roles.q("date")
    filt = f" AND {where}" if where else ""
    row = ctx.db.one(
        f"SELECT min({d}) AS first, max({d}) AS last FROM data WHERE {d} IS NOT NULL{filt}"
    )
    if not row or row["last"] is None:
        raise UnprocessableError("The scope has no dated rows.", code="not_enough_data")
    first, last = as_date(row["first"]), as_date(row["last"])
    nxt = last + timedelta(days=1)
    end = date(nxt.year, nxt.month, 1)  # exclusive: the first day after the last complete month
    first_full = (
        first if first.day == 1 else shift_period(date(first.year, first.month, 1), "month", 1)
    )
    start = max(shift_period(end, "month", -BASELINE_MONTHS), first_full)
    months = (end.year - start.year) * 12 + end.month - start.month
    return start, end, months


def baseline(ctx: AnalysisContext, where: str) -> Baseline:
    """Monthly totals of the last complete months (evidence) and their yearly scale."""
    start, end, months = _month_window(ctx, where)
    if months < MIN_BASELINE_MONTHS:
        raise UnprocessableError(
            f"Only {max(months, 0)} complete months of data; a what-if needs at least "
            f"{MIN_BASELINE_MONTHS}.",
            code="not_enough_data",
        )
    d, rev = ctx.roles.q("date"), revenue_expr(ctx)
    units = f"sum({ctx.roles.q('quantity')})" if ctx.roles.has("quantity") else "NULL"
    cost = f"sum({ctx.roles.q('cost')})" if ctx.roles.has("cost") else "NULL"
    rate = discount_rate_expr(ctx)
    qty = ctx.roles.q("quantity") if ctx.roles.has("quantity") else "1"
    discount = f"sum({rate} * {qty}) / nullif(sum({qty}), 0)" if rate else "NULL"
    filt = f" AND {where}" if where else ""
    sql = (
        f"SELECT date_trunc('month', {d}) AS month, round(sum({rev}), 2) AS revenue, "
        f"round({units}, 4) AS units, round({cost}, 2) AS cost, "
        f"round({discount}, 6) AS discount_rate FROM data "
        f"WHERE {d} >= {lit(start)} AND {d} < {lit(end)}{filt} GROUP BY 1 ORDER BY 1"
    )
    rows = ctx.db.rows(sql)
    revenue = np.array([num(r["revenue"]) for r in rows])
    scale = 12.0 / months
    has_units = ctx.roles.has("quantity")
    has_cost = ctx.roles.has("cost")
    unit_list = [num(r["units"]) for r in rows] if has_units else []
    cost_arr = np.array([num(r["cost"]) for r in rows]) if has_cost else np.array([])
    rates = [num(r["discount_rate"]) for r in rows if r["discount_rate"] is not None]
    return Baseline(
        start=start,
        end=end - timedelta(days=1),
        months=months,
        revenue=float(revenue.sum()) * scale,
        units=float(sum(unit_list)) * scale if has_units else None,
        cost=float(cost_arr.sum()) * scale if has_cost else None,
        discount_rate=float(np.mean(rates)) if rates else None,
        revenue_h=_half_width(revenue),
        cost_h=_half_width(cost_arr) if has_cost else 0.0,
        monthly_units=unit_list,
        monthly_unit_cost=(
            [c / u for c, u in zip(cost_arr.tolist(), unit_list, strict=True) if u > 0]
            if has_cost and has_units
            else []
        ),
        evidence=evidence_query("Monthly totals of the baseline period", sql, rows),
    )


def _half_width(values: np.ndarray) -> float:
    """Relative 95% half-width of the yearly total from month-to-month variation."""
    if len(values) < 2 or float(values.mean()) == 0:
        return 0.0
    return float(Z95 * values.std(ddof=1) / abs(values.mean()) / math.sqrt(len(values)))


def _range(values: list[float]) -> dict[str, float]:
    return {"low": round(min(values), 2), "high": round(max(values), 2)}


def _band(expected: float, candidates: list[float]) -> dict[str, float]:
    return {**_range([*candidates, expected]), "expected": round(expected, 2)}


def _observed(values: list[float]) -> tuple[float, float] | None:
    """p5..p95 of values relative to their median, in percent."""
    clean = [v for v in values if v > 0]
    if len(clean) < 3:
        return None
    med = float(np.median(clean))
    rel = 100.0 * (np.array(clean) / med - 1.0)
    return float(np.quantile(rel, 0.05)), float(np.quantile(rel, 0.95))


def _extrapolation(what: str, change: float, seen: tuple[float, float] | None) -> str | None:
    if seen is None:
        return (
            f"Extrapolation risk: the data does not show enough variation in {what} to say "
            f"whether a {change:+.0f}% change has been seen before."
        )
    low, high = seen
    if low <= change <= high:
        return None
    return (
        f"Extrapolation risk: the data only shows {what} between {low:+.0f}% and {high:+.0f}% of "
        f"usual; a {change:+.0f}% change is outside that range, so the estimate is less reliable."
    )


def _chain(ctx: AnalysisContext, scenario: Scenario) -> list[tuple[str | None, str, str, str]]:
    """Scopes to try, narrowest first: (item role, scope, name, SQL filter).

    A category is fitted on its weekly totals (one price index per week): its prices usually move
    together, and on the planted data this grain's 95% intervals hold the true value in 29 of 30
    files, against 26 of 30 for product-level rows of the same weeks.
    """
    pooled = (
        "category" if ctx.roles.has("category") else "product" if ctx.roles.has("product") else None
    )
    chain: list[tuple[str | None, str, str, str]] = []
    if scenario.scope == "product" and scenario.target:
        where = f"{ctx.roles.q('product')} = {quote_literal(scenario.target)}"
        chain.append(("product", "product", scenario.target, where))
        if ctx.roles.has("category"):
            category = ctx.db.scalar(
                f"SELECT mode({ctx.roles.q('category')}) FROM data WHERE {where}"
            )
            if category is not None:
                chain.append(
                    (
                        "category",
                        "category",
                        str(category),
                        f"{ctx.roles.q('category')} = {quote_literal(str(category))}",
                    )
                )
    elif scenario.scope == "category" and scenario.target:
        where = f"{ctx.roles.q('category')} = {quote_literal(scenario.target)}"
        chain.append(("category", "category", scenario.target, where))
    chain.append((pooled, "all", "all items", ""))
    return chain


@dataclass
class _Search:
    """The scopes tried for an elasticity, the one used (if any) and the SQL behind each."""

    used: Estimate | None
    tried: list[dict[str, Any]]
    notes: list[str]
    evidence: list[dict[str, Any]]  # the periods SQL and rows of every scope tried


def _elasticity(ctx: AnalysisContext, scenario: Scenario, need_discount: bool) -> _Search:
    """Walk product -> category -> all and stop at the first usable estimate."""
    search = _Search(None, [], [], [])
    for role, scope, name, where in _chain(ctx, scenario):
        est, sql, rows = fit_group(ctx, role, scope, name, where)
        search.tried.append(est.as_dict())
        search.evidence.append(
            evidence_query(
                f"Units and price index per item and period ({scope} '{name}')", sql, rows
            )
        )
        if est.usable() and (not need_discount or _discount_ok(est)):
            search.used = est
            return search
        reason = est.reason if not est.usable() else f"{UNRELIABLE_TEXT} for discounts"
        search.notes.append(f"The {scope} estimate ({name}) was not used: {reason}.")
    return search


def _add_search(result: dict[str, Any], search: _Search) -> None:
    """Record the estimates tried, the notes and the SQL of every estimate as evidence."""
    result["estimates_tried"] = search.tried
    result["warnings"].extend(search.notes)
    used = search.used
    if used is not None and used.items > 1 and used.items_with_price_changes < used.items:
        result["warnings"].append(
            f"Prices varied in only {used.items_with_price_changes} of the {used.items} groups "
            f"behind the {used.scope} estimate; it mostly reflects those and is applied to all."
        )
    result["evidence"].extend(search.evidence)


def _discount_ok(est: Estimate) -> bool:
    if est.discount_effect is None or est.discount_ci is None:
        return False
    low, high = est.discount_ci
    return not (low <= 0 <= high) and est.discount_effect > 0


def run(ctx: AnalysisContext, scenario: Scenario) -> dict[str, Any]:
    """Compute one scenario."""
    if abs(scenario.change_pct) > MAX_CHANGE_PCT:
        raise UnprocessableError(
            f"Changes are capped at +/-{MAX_CHANGE_PCT:.0f}%.", code="change_too_large"
        )
    where = _scope_filter(ctx, scenario.scope, scenario.target)
    base = baseline(ctx, where)
    c = scenario.change_pct / 100.0
    result: dict[str, Any] = {
        "lever": scenario.lever,
        "change_pct": scenario.change_pct,
        "scope": scenario.scope,
        "target": scenario.target,
        "baseline": {
            "from": base.start.isoformat(),
            "to": base.end.isoformat(),
            "months": base.months,
            "yearly_revenue": round(base.revenue, 2),
            "yearly_units": None if base.units is None else round(base.units, 2),
            "yearly_cost": None if base.cost is None else round(base.cost, 2),
            "normal_variation_pct": round(100 * base.revenue_h, 2),
        },
        "warnings": [],
        "evidence": [base.evidence],
        "elasticity": None,
        "estimates_tried": [],
    }
    assumptions = [
        f"The next 12 months look like {base.start.isoformat()} to {base.end.isoformat()}, "
        "within normal month-to-month variation "
        f"(+/-{fmt_number(100 * base.revenue_h, 'en', 1)}%).",
        f"Only the {scenario.lever} changes; season, competitors, marketing and stock stay as "
        "they were.",
    ]
    if base.months < BASELINE_MONTHS:
        result["warnings"].append(
            f"Only {base.months} complete months are available; they were scaled to a year."
        )
    handler = {"price": _price, "discount": _discount, "volume": _volume, "cost": _cost}[
        scenario.lever
    ]
    handler(ctx, scenario, c, base, where, result, assumptions)
    if base.cost is None and scenario.lever != "cost":
        result["profit_change"] = None
        result["warnings"].append("Profit not available: the dataset has no cost column.")
    result["assumptions"] = assumptions
    result["extrapolation_risk"] = any(
        w.startswith("Extrapolation risk") for w in result["warnings"]
    )
    result["confidence"] = _confidence(result, base)
    est = result.get("elasticity")
    if result.get("status") == "not_enough_evidence":
        result["sample_size"] = 0
    elif est:
        result["sample_size"] = est["periods"]  # the periods behind the elasticity used
    else:
        result["sample_size"] = base.months
    result["value"] = result["revenue_change"]
    result["method"] = METHODS[scenario.lever]
    result["summary"] = _summary(result)
    return result


def _confidence(result: dict[str, Any], base: Baseline) -> str:
    if result.get("status") == "not_enough_evidence":
        return "low"
    if result["extrapolation_risk"] or base.months < BASELINE_MONTHS:
        return "low"
    est = result.get("elasticity")
    if est:
        key, ci_key = (
            ("discount_effect", "discount_ci95")
            if result["lever"] == "discount"
            else ("elasticity", "ci95")
        )
        if est.get(ci_key) and (est[ci_key][1] - est[ci_key][0]) / 2 > abs(est[key]):
            return "low"
    return "medium"  # observational data: never high


def _not_enough(result: dict[str, Any], notes: list[str], tried: list[dict[str, Any]]) -> None:
    result["status"] = "not_enough_evidence"
    result["revenue_change"] = None
    result["profit_change"] = None
    result["warnings"].extend(notes)
    result["message"] = (
        f"Not enough evidence: {UNRELIABLE_TEXT}, so no revenue range is given."
        if any(t["status"] == "unreliable" for t in tried)
        else "Not enough evidence: prices (or discounts) did not vary enough to measure their "
        "effect, so no revenue range is given."
    )


def _price(
    ctx: AnalysisContext,
    s: Scenario,
    c: float,
    base: Baseline,
    where: str,
    result: dict[str, Any],
    assumptions: list[str],
) -> None:
    reason = available(ctx)
    if reason:
        _not_enough(result, [f"Price elasticity cannot be estimated: {reason}."], [])
        return
    search = _elasticity(ctx, s, need_discount=False)
    _add_search(result, search)
    est = search.used
    if est is None:
        _not_enough(result, [], search.tried)
        return
    result["status"] = "ok"
    result["elasticity"] = est.as_dict()
    point, low, high = est.elasticity or 0.0, est.ci_low or 0.0, est.ci_high or 0.0
    _price_effect(c, base, point, (low, high), result)
    warn = _extrapolation("prices", 100 * c, est.price_range_pct)
    if warn:
        result["warnings"].append(warn)
    assumptions.append(
        f"Units respond with the price elasticity measured on {est.scope} '{est.name}': "
        f"{est.elasticity:.2f} (95% interval {low:.2f} to {high:.2f}), from {est.periods} "
        f"{est.grain}ly totals with controls for season, trend"
        + (" and discount" if est.discount_effect is not None else "")
        + "."
    )
    assumptions.append("Unit cost stays the same per unit; only the number of units changes cost.")
    assumptions.append("This is an association in past sales, not a controlled price test.")


def _price_effect(
    c: float, base: Baseline, point: float, ci: tuple[float, float], result: dict[str, Any]
) -> None:
    def revenue(e: float, b: float) -> float:
        return b * (math.pow(1 + c, 1 + e) - 1)

    def profit(e: float, b: float, cost: float) -> float:
        return revenue(e, b) - cost * (math.pow(1 + c, e) - 1)

    h = base.revenue_h
    bases = [base.revenue * (1 - h), base.revenue * (1 + h)]
    rev = [revenue(e, b) for e in ci for b in bases]
    result["revenue_change"] = _band(revenue(point, base.revenue), rev)
    result["units_change_pct"] = _band(
        100 * ((1 + c) ** point - 1), [100 * ((1 + c) ** e - 1) for e in ci]
    )
    if base.cost is not None:
        costs = [base.cost * (1 - h), base.cost * (1 + h)]
        prof = [profit(e, b, k) for e in ci for b, k in zip(bases, costs, strict=True)]
        result["profit_change"] = _band(profit(point, base.revenue, base.cost), prof)


def _discount(
    ctx: AnalysisContext,
    s: Scenario,
    c: float,
    base: Baseline,
    where: str,
    result: dict[str, Any],
    assumptions: list[str],
) -> None:
    if not ctx.roles.has("discount"):
        raise UnprocessableError("This dataset has no discount column.", code="invalid_lever")
    rate = base.discount_rate or 0.0
    new_rate = rate * (1 + c)
    if new_rate >= MAX_DISCOUNT_RATE:
        raise UnprocessableError(
            f"The average discount rate would reach {100 * new_rate:.0f}% "
            f"(from {100 * rate:.0f}%); "
            f"a what-if needs it below {100 * MAX_DISCOUNT_RATE:.0f}%.",
            code="change_too_large",
        )
    if rate <= 0:
        _not_enough(
            result,
            [
                "There were no discounts in the baseline period, so a relative change has "
                "nothing to change."
            ],
            [],
        )
        return
    reason = available(ctx)
    if reason:
        _not_enough(result, [f"The discount effect cannot be estimated: {reason}."], [])
        return
    search = _elasticity(ctx, s, need_discount=True)
    _add_search(result, search)
    est = search.used
    if est is None or est.discount_effect is None or est.discount_ci is None:
        _not_enough(result, [], search.tried)
        return
    result["status"] = "ok"
    result["elasticity"] = est.as_dict()
    price_factor = (1 - new_rate) / (1 - rate)

    def units(b: float) -> float:
        return math.exp(b * (new_rate - rate))

    h = base.revenue_h
    bases = [base.revenue * (1 - h), base.revenue * (1 + h)]
    lo, hi = est.discount_ci
    rev = [b * (units(k) * price_factor - 1) for k in (lo, hi) for b in bases]
    result["revenue_change"] = _band(
        base.revenue * (units(est.discount_effect) * price_factor - 1), rev
    )
    if base.cost is not None:
        costs = [base.cost * (1 - h), base.cost * (1 + h)]
        prof = [
            b * (units(k) * price_factor - 1) - cost * (units(k) - 1)
            for k in (lo, hi)
            for b, cost in zip(bases, costs, strict=True)
        ]
        point = units(est.discount_effect)
        result["profit_change"] = _band(
            base.revenue * (point * price_factor - 1) - base.cost * (point - 1), prof
        )
    seen = None
    if est.discount_range is not None and rate > 0:
        seen = (100 * (est.discount_range[0] / rate - 1), 100 * (est.discount_range[1] / rate - 1))
    warn = _extrapolation("average discount rates", 100 * c, seen)
    if warn:
        result["warnings"].append(warn)
    assumptions.append(
        f"The average discount rate moves from {fmt_pct(100 * rate, 'en')} to "
        f"{fmt_pct(100 * new_rate, 'en')}; units respond as measured on {est.scope} '{est.name}' "
        f"(+{est.discount_effect:.2f} log units per +1.0 of discount rate, 95% interval "
        f"{lo:.2f} to {hi:.2f})."
    )
    assumptions.append("Revenue in the data is recorded after discount; list prices stay the same.")
    assumptions.append("This is an association in past sales, not a controlled discount test.")


def _volume(
    ctx: AnalysisContext,
    s: Scenario,
    c: float,
    base: Baseline,
    where: str,
    result: dict[str, Any],
    assumptions: list[str],
) -> None:
    if base.units is None:
        raise UnprocessableError("This dataset has no quantity column.", code="invalid_lever")
    # the mix range compares the groups inside the scope: categories for all items, products
    # inside a category (a single product has no mix)
    group = (
        "category"
        if ctx.roles.has("category") and s.scope == "all"
        else "product"
        if ctx.roles.has("product") and s.scope != "product"
        else None
    )
    d, rev, qty = ctx.roles.q("date"), revenue_expr(ctx), ctx.roles.q("quantity")
    cost = f"sum({ctx.roles.q('cost')})" if ctx.roles.has("cost") else "NULL"
    filt = f" AND {where}" if where else ""
    end_excl = base.end + timedelta(days=1)
    per_unit: list[tuple[float, float | None]] = []
    if group:
        sql = (
            f"SELECT {ctx.roles.q(group)} AS item, round(sum({rev}), 2) AS revenue, "
            f"round(sum({qty}), 4) AS units, round({cost}, 2) AS cost FROM data "
            f"WHERE {d} >= {lit(base.start)} AND {d} < {lit(end_excl)}{filt} "
            f"GROUP BY 1 HAVING sum({qty}) > 0 ORDER BY 1"
        )
        rows = ctx.db.rows(sql)
        result["evidence"].append(
            evidence_query(f"Revenue and units per {group} in the baseline", sql, rows)
        )
        for r in rows:
            u = num(r["units"])
            per_unit.append(
                (num(r["revenue"]) / u, None if r["cost"] is None else num(r["cost"]) / u)
            )
    extra = base.units * c
    h = base.revenue_h
    expected = base.revenue * c
    prices = [p for p, _ in per_unit] or [base.revenue / base.units]
    spread = [extra * p * f for p in (min(prices), max(prices)) for f in (1 - h, 1 + h)]
    result["status"] = "ok"
    result["revenue_change"] = _band(expected, spread)
    result["units_change_pct"] = {
        "low": round(100 * c, 2),
        "expected": round(100 * c, 2),
        "high": round(100 * c, 2),
    }
    if base.cost is not None:
        margins = [p - k for p, k in per_unit if k is not None] or [
            (base.revenue - base.cost) / base.units
        ]
        prof = [extra * m * f for m in (min(margins), max(margins)) for f in (1 - h, 1 + h)]
        result["profit_change"] = _band((base.revenue - base.cost) * c, prof)
    warn = _extrapolation("monthly units", 100 * c, _observed(base.monthly_units))
    if warn:
        result["warnings"].append(warn)
    assumptions.append(
        "Units change at today's prices; the range covers the extra units coming from the "
        f"lowest- to the highest-priced {group or 'item'}."
    )
    assumptions.append("Unit cost stays the same per unit.")


def _cost(
    ctx: AnalysisContext,
    s: Scenario,
    c: float,
    base: Baseline,
    where: str,
    result: dict[str, Any],
    assumptions: list[str],
) -> None:
    if base.cost is None:
        raise UnprocessableError("This dataset has no cost column.", code="invalid_lever")
    h = base.cost_h
    result["status"] = "ok"
    result["revenue_change"] = {"low": 0.0, "expected": 0.0, "high": 0.0}
    result["profit_change"] = _band(
        -base.cost * c, [-base.cost * (1 - h) * c, -base.cost * (1 + h) * c]
    )
    warn = _extrapolation("unit costs", 100 * c, _observed(base.monthly_unit_cost))
    if warn:
        result["warnings"].append(warn)
    assumptions.append(
        "Unit cost changes by the same percentage on every unit; prices and units stay the "
        "same, so revenue does not change."
    )


def approx(value: float) -> str:
    """Round an amount to 3 significant figures for text (the JSON keeps exact values)."""
    if value == 0:
        return "0"
    digits = 3 - int(math.floor(math.log10(abs(value)))) - 1
    rounded = round(value, digits)
    return f"{rounded:,.0f}" if digits <= 0 else f"{rounded:,.{digits}f}"


def _summary(result: dict[str, Any]) -> str:
    lever, change = result["lever"], result["change_pct"]
    scope = "all items" if result["scope"] == "all" else f"{result['scope']} '{result['target']}'"
    if result.get("status") == "not_enough_evidence":
        return f"{change:+.0f}% {lever} on {scope}: {result['message']}"
    rev = result["revenue_change"]
    text = (
        f"A {change:+.0f}% {lever} change on {scope} is estimated to change yearly revenue by "
        f"about {approx(rev['low'])} to {approx(rev['high'])} (central estimate "
        f"{approx(rev['expected'])})"
    )
    profit = result.get("profit_change")
    if profit:
        text += (
            f" and profit by about {approx(profit['low'])} to {approx(profit['high'])} "
            f"(central {approx(profit['expected'])})"
        )
    text += ". This is a range under the listed assumptions, not a promise."
    if result["extrapolation_risk"]:
        text += " The change is outside what the data has seen (extrapolation risk)."
    return text
