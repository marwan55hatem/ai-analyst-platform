"""Drivers: which factors (price, discount, category, region, channel, weekday) go with revenue.

Gradient boosting on log revenue per line, explained with SHAP and cross-checked with permutation
importance on later data. The result describes association, not proven cause, so confidence is
never above medium.
"""

from typing import Any

import numpy as np

from app.ml.anomaly.rows import discount_rate_expr
from app.ml.base import AnalysisContext, AnalysisModule, DataFacts, Insight, ModuleSkipped
from app.ml.drivers import model as driver_model
from app.ml.drivers.model import LABELS, NUMERIC
from app.ml.format import fmt_number, fmt_pct
from app.ml.modules.common import (
    SENSITIVITY,
    as_date,
    base_revenue,
    build_insight,
    evidence_query,
    excluded_rows,
    refund_rows,
    revenue_expr,
    revenue_key_columns,
)
from app.ml.quality import cap_confidence
from app.services.profiling.results import confidence_for

MIN_ROWS = 500
WEAK_FIT_R2 = 0.1
# A factor whose shuffling costs less than this R² on later lines has no clear link
CLEAR_LINK_R2_DROP = 0.005
ROLE_FACTORS = ("price", "discount", "category", "region", "channel")
ASSOCIATION_NOTE = (
    "This is an association found in past data, not proven cause: other things that changed at "
    "the same time may explain part of it."
)
EVIDENCE_LEVELS = 20


class DriverModule(AnalysisModule):
    """Factors most associated with revenue per line, with partial dependence for the top 3."""

    name = "drivers"
    title = "Revenue drivers"
    requires = frozenset({"date", "revenue"})
    requires_any = frozenset(ROLE_FACTORS)

    def data_requirement(self, facts: DataFacts) -> str | None:
        """Explain when there are too few rows to learn from."""
        if facts.row_count and facts.row_count < MIN_ROWS:
            return f"only {facts.row_count} rows; revenue drivers need at least {MIN_ROWS}"
        return None

    def run(self, context: AnalysisContext) -> list[Insight]:
        """Fit the driver model and return one insight."""
        ctx = context
        # requires_any guarantees one role factor; the weekday always comes from the date
        factors = [f for f in ROLE_FACTORS if ctx.roles.has(f)] + ["weekday"]
        rows, total = _rows(ctx, factors)
        if len(rows) < MIN_ROWS:
            raise ModuleSkipped(
                f"only {len(rows)} positive lines with a date; revenue drivers need at least "
                f"{MIN_ROWS}"
            )
        x = driver_model.frame(rows, factors)
        y = np.log1p(np.array([float(r["revenue"]) for r in rows]))
        days = [as_date(r["day"]) for r in rows]
        split = sorted(set(days))[int(len(set(days)) * (1 - driver_model.HOLDOUT_SHARE))]
        holdout = np.array([d >= split for d in days])
        if holdout.all() or not holdout.any():
            raise ModuleSkipped("the dates do not allow a later period to check the model on")
        result = driver_model.fit(
            x, y, holdout, ctx.settings.ml_seed, ctx.settings.driver_shap_rows
        )
        if total > len(rows):
            result.warnings.append(
                f"The model used a fixed sample of {fmt_number(len(rows), ctx.locale)} of "
                f"{fmt_number(total, ctx.locale)} lines (DRIVER_MAX_ROWS)."
            )
        return [_insight(ctx, result, split, x, len(rows))]


def _factor_sql(ctx: AnalysisContext, factor: str) -> str:
    if factor == "weekday":
        return f"dayname({ctx.roles.q('date')})"
    if factor == "discount":
        return f"CAST({discount_rate_expr(ctx)} AS DOUBLE)"
    if factor == "price":
        return f"CAST({ctx.roles.q('price')} AS DOUBLE)"
    return ctx.roles.q(factor)


def _rows(ctx: AnalysisContext, factors: list[str]) -> tuple[list[dict[str, Any]], int]:
    """Positive lines with a date: all, or a fixed hash sample of DRIVER_MAX_ROWS."""
    rev, d = revenue_expr(ctx), ctx.roles.q("date")
    where = f"WHERE {rev} > 0 AND {d} IS NOT NULL"
    total = int(ctx.db.scalar(f"SELECT count(*) FROM data {where}") or 0)
    limit = ctx.settings.driver_max_rows
    order = (
        f"ORDER BY md5(CAST(__row_number AS VARCHAR) || '{ctx.settings.ml_seed}') LIMIT {limit}"
        if total > limit
        else "ORDER BY __row_number"
    )
    cols = ", ".join(f"{_factor_sql(ctx, f)} AS {f}" for f in factors)
    rows = ctx.db.rows(
        f"SELECT CAST({rev} AS DOUBLE) AS revenue, {d} AS day, {cols} FROM data {where} {order}"
    )
    return rows, total


def _evidence(ctx: AnalysisContext, factor: str, x: Any) -> dict[str, Any]:
    """Average revenue per line by level (or by fixed bands for numbers): the raw association."""
    rev, d = revenue_expr(ctx), ctx.roles.q("date")
    expr = _factor_sql(ctx, factor)
    where = f"WHERE {rev} > 0 AND {d} IS NOT NULL AND {expr} IS NOT NULL"
    if factor in NUMERIC and x[factor].nunique() > EVIDENCE_LEVELS:
        cuts = sorted(
            {round(float(q), 4) for q in np.quantile(x[factor].dropna(), [0.2, 0.4, 0.6, 0.8])}
        )
        band = (
            "CASE "
            + " ".join(f"WHEN {expr} < {c} THEN {i + 1}" for i, c in enumerate(cuts))
            + f" ELSE {len(cuts) + 1} END"
        )
        sql = (
            f"SELECT {band} AS band, round(min({expr}), 4) AS band_from, "
            f"round(max({expr}), 4) AS band_to, count(*) AS lines, "
            f"round(avg({rev}), 2) AS avg_revenue FROM data {where} GROUP BY 1 ORDER BY 1"
        )
    else:
        level = f"round({expr}, 4)" if factor in NUMERIC else expr
        sql = (
            f"SELECT {level} AS level, count(*) AS lines, round(avg({rev}), 2) AS avg_revenue "
            f"FROM data {where} GROUP BY 1 ORDER BY 1 LIMIT {EVIDENCE_LEVELS}"
        )
    return evidence_query(
        f"Average revenue per line by {LABELS[factor]} (raw association, no controls)",
        sql,
        ctx.db.rows(sql),
    )


def _pd_text(ctx: AnalysisContext, factor: str, points: list[dict[str, Any]]) -> str:
    if len(points) < 2:
        return ""
    low = min(points, key=lambda p: p["relative_revenue_pct"])
    high = max(points, key=lambda p: p["relative_revenue_pct"])

    def show(p: dict[str, Any]) -> str:
        value = p["value"]
        if factor == "discount":
            return fmt_pct(100 * float(value), ctx.locale, 0)
        return value if isinstance(value, str) else fmt_number(float(value), ctx.locale, 2)

    gap = 100.0 * (
        (1 + high["relative_revenue_pct"] / 100.0) / (1 + low["relative_revenue_pct"] / 100.0) - 1
    )
    return (
        f"With the other factors held at their observed mix, lines at {LABELS[factor]} "
        f"{show(high)} average about {fmt_pct(gap, ctx.locale, 0)} more revenue than at "
        f"{show(low)}."
    )


def _insight(
    ctx: AnalysisContext, result: driver_model.DriverResult, split: Any, x: Any, rows: int
) -> Insight:
    loc = ctx.locale
    ranking = result.ranking()
    top = ranking[:3]
    shares = ", ".join(f"{LABELS[f]} ({fmt_pct(result.shap_share[f], loc, 0)})" for f in top)
    weak = not result.r2_holdout >= WEAK_FIT_R2
    agree = result.agrees()
    check = (
        f"Permutation importance on lines from {split.isoformat()} onwards names the same top "
        "factor."
        if agree
        else (
            "Permutation importance on later lines ranks "
            f"{LABELS[result.permutation_ranking()[0]]} first instead, so the order is uncertain."
        )
    )
    fit_text = (
        f"The factors explain little of the variation in later lines (R² "
        f"{fmt_number(result.r2_holdout, loc, 2)}), so treat this as a weak pattern. "
        if weak
        else f"On later lines the model explains R² {fmt_number(result.r2_holdout, loc, 2)} of "
        "the variation in log revenue per line. "
    )
    clear = [f for f in top if result.permutation[f] >= CLEAR_LINK_R2_DROP]
    unclear = [f for f in ranking if f not in clear]
    pd_texts = [t for f in clear if (t := _pd_text(ctx, f, result.partial_dependence.get(f, [])))]
    if unclear:
        pd_texts.append(
            "No clear link on later lines for: " + ", ".join(LABELS[f] for f in unclear) + "."
        )
    summary = (
        f"Revenue per line is most strongly associated with {shares} (share of SHAP importance). "
        f"{check} {fit_text}{' '.join(pd_texts)} {ASSOCIATION_NOTE}"
    )
    if "price" in result.factors:
        summary += (
            " Price is part of how a line's revenue is calculated, so part of its link is "
            "mechanical."
        )
    level = "low" if weak or not agree else "medium"
    level = cap_confidence(level, confidence_for(rows))
    warnings = list(result.warnings)
    refunds = refund_rows(ctx)
    if refunds:
        warnings.append(
            f"{fmt_number(refunds, loc)} refund rows were left out: drivers describe positive "
            "sales."
        )
    keys = [ctx.roles.columns["date"], *revenue_key_columns(ctx)]
    keys += [ctx.roles.columns[f] for f in result.factors if f in ctx.roles.columns]
    importance = [
        {
            "factor": f,
            "label": LABELS[f],
            "shap_share_pct": round(result.shap_share[f], 2),
            "permutation_r2_drop": round(result.permutation[f], 4),
            "clear_link": result.permutation[f] >= CLEAR_LINK_R2_DROP,
        }
        for f in ranking
    ]
    return build_insight(
        ctx,
        module="drivers",
        type="revenue_drivers",
        title=f"{LABELS[top[0]].capitalize()} is the factor most associated with revenue per line",
        summary=summary,
        value=[i["factor"] for i in importance],
        sample_size=rows,
        method=(
            "LightGBM on log(1 + revenue) per positive line; time split (last 20% of dates held "
            "out); SHAP TreeExplainer mean |SHAP| on a fixed sample; permutation importance (R² "
            "drop, 5 repeats) on the holdout; partial dependence for the top 3 factors"
        ),
        key_columns=keys,
        money_effect=SENSITIVITY * base_revenue(ctx),
        payload={
            "importance": importance,
            "partial_dependence": result.partial_dependence,
            "r2_holdout": round(result.r2_holdout, 4),
            "methods_agree_on_top": agree,
            "holdout_from": split.isoformat(),
            "train_rows": result.train_rows,
            "holdout_rows": result.holdout_rows,
            "shap_rows": result.shap_rows,
            "target": "log(1 + revenue) per positive line",
            "association_not_causation": True,
        },
        chart_spec={
            "type": "bar",
            "title": "Factors associated with revenue per line (share of SHAP importance)",
            "x": "label",
            "y": ["shap_share_pct"],
            "data": "payload.importance",
        },
        evidence=[_evidence(ctx, f, x) for f in top],
        excluded=excluded_rows(ctx, [ctx.roles.columns["date"], *revenue_key_columns(ctx)]),
        confidence=level,  # type: ignore[arg-type]
        warnings=tuple(warnings),
        what="lines",
    )
