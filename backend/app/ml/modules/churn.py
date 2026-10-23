"""Churn risk: which active customers are at high estimated risk of not buying again, and why.

Purchase mode (default): label = no positive purchase within W days after a cutoff; train on the
earlier cutoff, check on the later one, refit on the later one and score every customer active in
the last year with features as of the last purchase date. Flag mode (a `churn_flag` column): the
flag is the label, the check is cross-validated and time-sensitive features are left out.
"""

import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from app.ml.base import AnalysisContext, AnalysisModule, DataFacts, Insight, ModuleSkipped
from app.ml.budget import Budget
from app.ml.churn import labels as churn_labels
from app.ml.churn import model as churn_model
from app.ml.churn.features import (
    LABELS,
    TREND_DAYS,
    FeatureSet,
    features_sql,
    flagged_sql,
    labelled_sql,
)
from app.ml.churn.features import matrix as feature_matrix
from app.ml.format import fmt_money, fmt_number, fmt_pct
from app.ml.modules.common import (
    as_date,
    build_insight,
    evidence_query,
    excluded_rows,
    refund_rows,
    revenue_expr,
    revenue_key_columns,
)
from app.ml.modules.segments import pseudonym
from app.ml.quality import cap_confidence
from app.services.profiling.results import confidence_for

MIN_CUSTOMERS = 500
MIN_CHURNERS = 50
MIN_RATE, MAX_RATE = 0.03, 0.97
HIGH_RISK, MEDIUM_RISK = 0.5, 0.25
RANK_SHARE = 0.10
RANK_LEAD_RATE = 0.10
TOP_BAND, REST_BAND = "top_10pct", "rest"
SCORE_BATCH = 5000
CACHE_KEY = "churn_scores"
ESTIMATE_NOTE = (
    "These are estimated risks learned from past purchases, not a certainty about any single "
    "customer."
)
_PHRASES = {  # feature -> (text before the value, text after it, decimals)
    "frequency": ("", " orders", 0),
    "tenure_days": ("customer for ", " days", 0),
    "avg_gap_days": ("buys every ", " days on average", 1),
    "category_count": ("bought from ", " categories", 0),
}


@dataclass
class _Fit:
    """Everything the insights and the per-customer rows need from one churn run."""

    features: FeatureSet
    comparison: churn_model.Comparison
    fitted: churn_model.Fitted
    rows: list[dict[str, Any]]
    x: np.ndarray
    p: np.ndarray
    label_rate: float
    plan: churn_labels.Plan | None
    evidence: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    contributions: np.ndarray | None = None  # SHAP per scored customer, computed once


class ChurnModule(AnalysisModule):
    """Estimated churn risk per customer with revenue at stake and the main reasons."""

    name = "churn"
    title = "Churn risk"
    requires = frozenset({"customer_id", "date", "revenue"})

    def data_requirement(self, facts: DataFacts) -> str | None:
        """Explain when there are too few customers to learn churn from."""
        customers = facts.distinct.get("customer_id")
        if customers is not None and customers < MIN_CUSTOMERS:
            return f"only {customers} customers; churn risk needs at least {MIN_CUSTOMERS}"
        return None

    def run(self, context: AnalysisContext) -> list[Insight]:
        """Fit, check and explain the churn model; leave the per-customer rows for the service."""
        ctx = context
        rev, d = revenue_expr(ctx), ctx.roles.q("date")
        last = ctx.db.scalar(f"SELECT max({d}) FROM data WHERE {rev} > 0")
        if last is None:
            raise ModuleSkipped("no positive purchase has a date")
        end = as_date(last)
        fit = _fit_flagged(ctx, end) if ctx.roles.has("churn_flag") else _fit_by_time(ctx, end)
        typical = {n: float(np.median(fit.x[:, i])) for i, n in enumerate(fit.features.names)}
        importance = _importance(fit)
        insights = [
            _risk_insight(ctx, fit, importance),
            _model_insight(ctx, fit),
        ]
        ctx.cache[CACHE_KEY] = lambda: _scores(ctx, fit, typical)
        return insights


def _check_labels(y: np.ndarray, where: str) -> None:
    churners = int(y.sum())
    if churners < MIN_CHURNERS:
        raise ModuleSkipped(
            f"only {churners} customers churned {where}; churn risk needs at least {MIN_CHURNERS} "
            "churners to learn from"
        )
    rate = churners / len(y)
    if not MIN_RATE <= rate <= MAX_RATE:
        raise ModuleSkipped(
            f"{100 * rate:.1f}% of customers churned {where}, outside the 3-97% range a model can "
            "learn from"
        )


def _sample(ctx: AnalysisContext, rows: list[dict[str, Any]], what: str) -> list[dict[str, Any]]:
    limit = ctx.settings.churn_max_customers
    if len(rows) <= limit:
        return rows
    rng = np.random.default_rng(ctx.settings.ml_seed)
    keep = np.sort(rng.choice(len(rows), size=limit, replace=False))
    ctx.warn(
        f"{fmt_number(len(rows), ctx.locale)} customers at the {what} cutoff: the model used a "
        f"fixed random sample of {fmt_number(limit, ctx.locale)} (CHURN_MAX_CUSTOMERS)."
    )
    return [rows[i] for i in keep]


def _labelled(
    ctx: AnalysisContext, features: FeatureSet, cutoff: date, window: int, what: str
) -> tuple[np.ndarray, np.ndarray]:
    rows = _sample(ctx, ctx.db.rows(labelled_sql(ctx, cutoff, window)), what)
    if not rows:
        raise ModuleSkipped(f"no customer was active in the year before the {what} cutoff")
    y = np.array([int(r["churned"]) for r in rows], dtype=int)
    _check_labels(y, f"after the {what} cutoff ({cutoff.isoformat()})")
    return feature_matrix(rows, features), y


def _fit_by_time(ctx: AnalysisContext, end: date) -> _Fit:
    gap = ctx.db.one(churn_labels.median_gap_sql(ctx))
    if not gap or not gap.get("gaps"):
        raise ModuleSkipped(
            "no customer bought on two different days, so no churn window can be set"
        )
    plan = churn_labels.plan_for(end, float(gap["median_gap_days"]))
    d, rev = ctx.roles.q("date"), revenue_expr(ctx)
    first = as_date(ctx.db.scalar(f"SELECT min({d}) FROM data WHERE {rev} > 0"))
    history = (plan.train_cutoff - first).days
    if history < plan.history_needed():
        raise ModuleSkipped(
            f"only {max(history, 0)} days of purchase history before the training cutoff; churn "
            f"risk needs at least {plan.history_needed()} (with a {plan.window_days}-day window)"
        )
    features = FeatureSet.for_context(ctx)
    train = _labelled(ctx, features, plan.train_cutoff, plan.window_days, "training")
    check = _labelled(ctx, features, plan.eval_cutoff, plan.window_days, "evaluation")
    budget = Budget(ctx.settings.churn_max_seconds, float("inf"))
    comparison = churn_model.compare_out_of_time(
        train, check, ctx.settings.ml_seed, lambda: budget.exhausted() is not None
    )
    fitted = churn_model.fit_final(comparison.winner, *check, ctx.settings.ml_seed)
    rows = ctx.db.rows(features_sql(ctx, end))
    x = feature_matrix(rows, features)
    evidence = [
        _counts_evidence(ctx, plan.train_cutoff, plan.window_days, "training"),
        _counts_evidence(ctx, plan.eval_cutoff, plan.window_days, "evaluation"),
        _profile_evidence(ctx, plan),
    ]
    return _Fit(
        features,
        comparison,
        fitted,
        rows,
        x,
        fitted.predict(x),
        float(check[1].mean()),
        plan,
        evidence,
        list(comparison.warnings),
    )


def _fit_flagged(ctx: AnalysisContext, end: date) -> _Fit:
    features = FeatureSet.for_context(ctx, flag_mode=True)
    sql = flagged_sql(ctx, end)
    labelled = ctx.db.rows(sql)
    if not labelled:
        raise ModuleSkipped("the churn_flag column has no recognisable yes/no values")
    y = np.array([int(r["churned"]) for r in labelled], dtype=int)
    _check_labels(y, "according to the churn_flag column")
    x_all = feature_matrix(labelled, features)
    comparison = churn_model.compare_cross_validated(x_all, y, ctx.settings.ml_seed)
    fitted = churn_model.fit_final(comparison.winner, x_all, y, ctx.settings.ml_seed)
    keep = [i for i, r in enumerate(labelled) if int(r["churned"]) == 0]
    rows = [labelled[i] for i in keep]
    x = x_all[keep]
    counts_sql = (
        f"WITH flagged AS ({sql}) SELECT count(*) AS customers, sum(churned) AS churned, "
        "round(100.0 * sum(churned) / count(*), 2) AS churn_rate_pct FROM flagged"
    )
    evidence = [
        evidence_query(
            "Customers and churners according to the churn_flag column",
            counts_sql,
            ctx.db.rows(counts_sql),
        )
    ]
    warning = (
        "Churn comes from the churn_flag column, which has no date: days since the last purchase "
        "and the spend trend are left out (they could reflect behaviour after leaving), the check "
        "uses 5-fold cross-validation instead of a later period, and confidence is capped at "
        "medium."
    )
    return _Fit(
        features,
        comparison,
        fitted,
        rows,
        x,
        fitted.predict(x) if len(rows) else np.array([], dtype=float),
        float(y.mean()),
        None,
        evidence,
        [warning],
    )


def _counts_evidence(ctx: AnalysisContext, cutoff: date, window: int, what: str) -> dict[str, Any]:
    sql = churn_labels.label_counts_sql(ctx, cutoff, window)
    return evidence_query(
        f"Active customers at the {what} cutoff and how many did not buy again within "
        f"{window} days",
        sql,
        ctx.db.rows(sql),
    )


def _profile_evidence(ctx: AnalysisContext, plan: churn_labels.Plan) -> dict[str, Any]:
    sql = (
        f"WITH base AS ({labelled_sql(ctx, plan.eval_cutoff, plan.window_days)}) "
        "SELECT churned, count(*) AS customers, round(avg(recency_days), 2) AS avg_recency_days, "
        "round(avg(frequency), 2) AS avg_orders, round(avg(monetary), 2) AS avg_spend, "
        "round(avg(tenure_days), 2) AS avg_tenure_days FROM base GROUP BY churned ORDER BY churned"
    )
    return evidence_query(
        f"Averages of churners (1) and stayers (0) at the evaluation cutoff "
        f"{plan.eval_cutoff.isoformat()}",
        sql,
        ctx.db.rows(sql),
    )


def _explain(fit: _Fit) -> np.ndarray:
    """SHAP values of every scored customer, in batches (used for reasons and importance)."""
    if fit.contributions is None:
        parts = [
            churn_model.explain(fit.fitted, fit.x[start : start + SCORE_BATCH])
            for start in range(0, len(fit.x), SCORE_BATCH)
        ]
        width = len(fit.features.names)
        fit.contributions = np.vstack(parts) if parts else np.empty((0, width))
    return fit.contributions


def _importance(fit: _Fit) -> list[dict[str, Any]]:
    """Global SHAP importance: mean |SHAP| over every scored customer."""
    if not len(fit.x):
        return []
    mean_abs = np.abs(_explain(fit)).mean(axis=0)
    total = float(mean_abs.sum()) or 1.0
    order = np.argsort(-mean_abs, kind="stable")
    return [
        {
            "feature": fit.features.names[i],
            "label": LABELS[fit.features.names[i]],
            "mean_abs_shap": round(float(mean_abs[i]), 6),
            "share_pct": round(100.0 * float(mean_abs[i]) / total, 2),
        }
        for i in order
    ]


def yearly_spend(monetary: float, tenure_days: float) -> float:
    """Spend per year, as in segmentation: spend x 365 / max(tenure, 365)."""
    return monetary * 365.0 / max(tenure_days, 365.0)


def risk_band(p: float) -> str:
    """Probability band: high >= 50%, medium >= 25%, otherwise low."""
    return "high" if p >= HIGH_RISK else "medium" if p >= MEDIUM_RISK else "low"


def _top_mask(fit: _Fit) -> np.ndarray:
    """Return the top 10% by estimated risk (ties broken by customer key)."""
    n = len(fit.p)
    top = max(1, int(round(RANK_SHARE * n))) if n else 0
    order = sorted(range(n), key=lambda i: (-fit.p[i], str(fit.rows[i]["customer"])))
    mask = np.zeros(n, dtype=bool)
    mask[order[:top]] = True
    return mask


def rank_led(max_probability: float, label_rate: float) -> str | None:
    """Return why the rank view leads (absolute probabilities are low), or None."""
    if max_probability < MEDIUM_RISK:
        return "no customer reaches a 25% estimated probability"
    if label_rate < RANK_LEAD_RATE:
        return f"only {100 * label_rate:.1f}% of customers churned in the checked period"
    return None


def _confidence(ctx: AnalysisContext, fit: _Fit) -> str:
    best = fit.comparison.scores[fit.comparison.winner]
    level = (
        "high"
        if best.auc >= 0.8 and best.churners >= 200
        else "medium"
        if best.auc >= 0.7
        else "low"
    )
    if fit.plan is None:
        level = cap_confidence(level, "medium")
    return cap_confidence(level, confidence_for(len(fit.p)))


def _drivers_text(importance: list[dict[str, Any]]) -> str:
    names = [i["label"] for i in importance[:3]]
    if not names:
        return ""
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f" The main drivers of the estimate are {joined}."


def _basis(fit: _Fit) -> str:
    if fit.plan is None:
        return "calibrated probability, from the churn_flag column"
    return f"calibrated probability of no purchase in the next {fit.plan.window_days} days"


def _risk_insight(ctx: AnalysisContext, fit: _Fit, importance: list[dict[str, Any]]) -> Insight:
    loc, cur = ctx.locale, ctx.currency
    stake = np.array(
        [yearly_spend(float(r["monetary"]), float(r["tenure_days"])) for r in fit.rows]
    )
    bands = np.array([risk_band(float(p)) for p in fit.p])
    top = _top_mask(fit)
    n = len(fit.p)
    count = {b: int((bands == b).sum()) for b in ("high", "medium", "low")}
    high = bands == "high"
    high_value, high_loss = float(stake[high].sum()), float((fit.p * stake)[high].sum())
    top_value, top_loss = float(stake[top].sum()), float((fit.p * stake)[top].sum())
    pmax = float(fit.p.max()) if n else 0.0
    lead = rank_led(pmax, fit.label_rate)
    top_text = (
        f"the 10% of customers with the highest estimated risk ({fmt_number(int(top.sum()), loc)}) "
        f"spend about {fmt_money(top_value, cur, loc)} a year; the expected loss among them is "
        f"{fmt_money(top_loss, cur, loc)}"
    )
    if lead:
        title = (
            "Top 10% of customers by estimated churn risk spend "
            f"{fmt_money(top_value, cur, loc)} a year"
        )
        summary = (
            f"Estimated churn risk for {fmt_number(n, loc)} active customers, ranked (basis: "
            f"rank by {_basis(fit)}; the rank view leads because {lead}). "
            f"{top_text[0].upper()}{top_text[1:]}. The absolute probabilities are low: the "
            f"highest is {fmt_pct(100 * pmax, loc)} and {fmt_number(count['high'], loc)} customers "
            "reach 50%."
        )
        money = top_loss
    else:
        title = f"{fmt_number(count['high'], loc)} customers are at high estimated churn risk"
        summary = (
            f"Estimated churn risk for {fmt_number(n, loc)} active customers "
            f"(basis: {_basis(fit)}). "
            f"{fmt_number(count['high'], loc)} customers are at high estimated risk (50% or more); "
            f"they spend about {fmt_money(high_value, cur, loc)} a year and the expected loss is "
            f"{fmt_money(high_loss, cur, loc)}. {fmt_number(count['medium'], loc)} are at medium "
            f"estimated risk (25-50%). Ranked by risk, {top_text}."
        )
        money = high_loss
    summary += _drivers_text(importance) + f" {ESTIMATE_NOTE}"
    warnings = [*fit.warnings, *_common_warnings(ctx)]
    keys = [ctx.roles.columns["customer_id"], ctx.roles.columns["date"], *revenue_key_columns(ctx)]
    return build_insight(
        ctx,
        module="churn",
        type="churn_risk",
        title=title,
        summary=summary,
        value={**count, TOP_BAND: int(top.sum())},
        sample_size=n,
        method=(
            f"{fit.comparison.winner} model, calibrated "
            f"({churn_model.calibration_method(len(fit.x))}), scored on features as of the last "
            "purchase date; reasons from SHAP values"
        ),
        key_columns=keys,
        money_effect=money,
        payload={
            "basis": "rank" if lead else "probability",
            "basis_reason": lead,
            "bands": {
                "high": {
                    "customers": count["high"],
                    "yearly_spend": round(high_value, 2),
                    "expected_loss": round(high_loss, 2),
                    "min_probability": HIGH_RISK,
                },
                "medium": {"customers": count["medium"], "min_probability": MEDIUM_RISK},
                "low": {"customers": count["low"]},
                TOP_BAND: {
                    "customers": int(top.sum()),
                    "yearly_spend": round(top_value, 2),
                    "expected_loss": round(top_loss, 2),
                },
            },
            "max_probability": round(pmax, 4),
            "importance": importance,
            "window_days": fit.plan.window_days if fit.plan else None,
            "scored_customers": n,
            "pseudonymized_customer_ids": "customer_id" in ctx.facts.pii_roles,
        },
        chart_spec={
            "type": "bar",
            "title": "What drives the estimated churn risk (mean |SHAP|)",
            "x": "label",
            "y": ["share_pct"],
            "data": "payload.importance",
        },
        evidence=fit.evidence,
        excluded=excluded_rows(ctx, keys),
        confidence=_confidence(ctx, fit),  # type: ignore[arg-type]
        warnings=tuple(warnings),
        what="customers",
    )


def _model_insight(ctx: AnalysisContext, fit: _Fit) -> Insight:
    loc = ctx.locale
    comp = fit.comparison
    best = comp.scores[comp.winner]
    scores = "; ".join(
        f"{kind}: AUC {fmt_number(m.auc, loc, 2)}, PR-AUC {fmt_number(m.pr_auc, loc, 2)}"
        for kind, m in comp.scores.items()
    )
    if fit.plan is not None:
        check = (
            f"To check the estimates, the model learned from customers as of "
            f"{fit.plan.train_cutoff.isoformat()} and was scored on what happened after "
            f"{fit.plan.eval_cutoff.isoformat()}"
        )
        label = (
            f" Churn means no purchase in the {fit.plan.window_days} days after a date: twice the "
            f"typical gap of {fmt_number(fit.plan.median_gap_days, loc, 1)} days between a "
            "customer's purchases, kept within 30-180 days."
        )
    else:
        check = "The estimates were checked with 5-fold cross-validation on the churn_flag column"
        label = ""
    poor = (
        f"The model separates leavers poorly (AUC {fmt_number(best.auc, loc, 2)}); treat the risk "
        "list as a rough ranking. "
        if best.auc < 0.65
        else ""
    )
    summary = (
        f"{poor}{check}: AUC {fmt_number(best.auc, loc, 2)} (0.5 = guessing, 1 = perfect), "
        f"PR-AUC {fmt_number(best.pr_auc, loc, 2)} against a churn rate of "
        f"{fmt_pct(100 * best.churners / max(best.customers, 1), loc)}, Brier score "
        f"{fmt_number(best.brier, loc, 3)}, and the 10% with the highest estimated risk churned "
        f"{fmt_number(best.top_decile_lift, loc, 1)}x as often as average. Models: {scores}. "
        f"The {comp.winner} model was chosen (higher PR-AUC).{label}"
    )
    keys = [ctx.roles.columns["customer_id"], ctx.roles.columns["date"], *revenue_key_columns(ctx)]
    return build_insight(
        ctx,
        module="churn",
        type="churn_model",
        title=f"Churn model check: AUC {fmt_number(best.auc, loc, 2)} on held-out customers",
        summary=summary,
        value={k: m.as_dict() for k, m in comp.scores.items()},
        sample_size=best.customers,
        method=(
            "logistic regression (log1p, standardized) and LightGBM, both calibrated; "
            + (
                "trained on the earlier cutoff, evaluated on the later one"
                if fit.plan
                else "stratified 5-fold cross-validation"
            )
        ),
        key_columns=keys,
        money_effect=0.0,
        payload={
            "models": {k: m.as_dict() for k, m in comp.scores.items()},
            "winner": comp.winner,
            "validation": comp.validation,
            "calibration": churn_model.calibration_method(len(fit.x)),
            "features": list(fit.features.names),
            "label": (
                {
                    "window_days": fit.plan.window_days,
                    "median_gap_days": round(fit.plan.median_gap_days, 2),
                    "train_cutoff": fit.plan.train_cutoff.isoformat(),
                    "eval_cutoff": fit.plan.eval_cutoff.isoformat(),
                    "end": fit.plan.end.isoformat(),
                    "lookback_days": churn_labels.LOOKBACK_DAYS,
                }
                if fit.plan
                else {"source": "churn_flag"}
            ),
        },
        chart_spec={
            "type": "bar",
            "title": "Churn model quality on held-out customers",
            "x": "model",
            "y": ["auc", "pr_auc"],
            "data": "payload.models",
        },
        evidence=fit.evidence,
        excluded=excluded_rows(ctx, keys),
        confidence=_confidence(ctx, fit),  # type: ignore[arg-type]
        warnings=tuple([*fit.warnings, *_common_warnings(ctx)]),
        what="customers",
    )


def _common_warnings(ctx: AnalysisContext) -> list[str]:
    out = []
    refunds = refund_rows(ctx)
    if refunds:
        out.append(
            f"{fmt_number(refunds, ctx.locale)} refund rows were left out: only positive purchases "
            "count as activity."
        )
    if "customer_id" in ctx.facts.pii_roles:
        out.append(
            "The customer id column looks like personal data, so customers are listed under "
            "pseudonyms, never their raw ids."
        )
    return out


def reason_text(
    name: str, value: float, typical: float, effect: str, locale: str, quiet: bool = False
) -> str:
    """Plain words for one reason, e.g. 'no purchase for 140 days (typical: 35) raises ...'.

    `quiet` marks a customer with no purchase in the last 180 days: both spend windows are empty,
    so the trend is described as "no spend" rather than "about level".
    """
    if name == "recency_days":
        text = (
            f"no purchase for {fmt_number(value, locale)} days "
            f"(typical: {fmt_number(typical, locale)})"
        )
    elif name == "spend_trend" and quiet:
        text = "no spend in the last 180 days"
    elif name == "spend_trend":
        change = 100.0 * (math.exp(value) - 1.0)
        direction = "down" if change < -5 else "up" if change > 5 else "about level"
        amount = f" {fmt_pct(abs(change), locale, 0)}" if direction != "about level" else ""
        text = f"spend in the last 90 days is {direction}{amount} on the 90 days before"
    elif name == "discount_share":
        text = (
            f"{fmt_pct(100 * value, locale, 0)} of spend bought on discount "
            f"(typical: {fmt_pct(100 * typical, locale, 0)})"
        )
    elif name == "monetary":
        text = f"total spend {fmt_number(value, locale)} (typical: {fmt_number(typical, locale)})"
    else:
        before, after, decimals = _PHRASES[name]
        text = (
            f"{before}{fmt_number(value, locale, decimals)}{after} "
            f"(typical: {fmt_number(typical, locale, decimals)})"
        )
    return f"{text} {effect} the estimated risk"


def _reasons(
    fit: _Fit, x: np.ndarray, contributions: np.ndarray, typical: dict[str, float], locale: str
) -> list[dict[str, Any]]:
    order = np.argsort(-np.abs(contributions), kind="stable")[:3]
    names = fit.features.names
    quiet = "recency_days" in names and x[names.index("recency_days")] >= 2 * TREND_DAYS
    out = []
    for i in order:
        name = fit.features.names[int(i)]
        effect = "raises" if contributions[i] > 0 else "lowers"
        out.append(
            {
                "feature": name,
                "value": round(float(x[i]), 4),
                "typical": round(typical[name], 4),
                "effect": effect,
                "shap": round(float(contributions[i]), 6),
                "text": reason_text(name, float(x[i]), typical[name], effect, locale, quiet),
            }
        )
    return out


def _scores(
    ctx: AnalysisContext, fit: _Fit, typical: dict[str, float]
) -> Iterator[list[dict[str, Any]]]:
    """Yield batches of per-customer rows for the churn_scores table."""
    private = "customer_id" in ctx.facts.pii_roles
    key, tenant = ctx.settings.pseudonym_key, str(ctx.tenant_id)
    top = _top_mask(fit)
    for start in range(0, len(fit.rows), SCORE_BATCH):
        stop = min(start + SCORE_BATCH, len(fit.rows))
        contributions = _explain(fit)[start:stop]
        batch = []
        for j, i in enumerate(range(start, stop)):
            r, p = fit.rows[i], float(fit.p[i])
            stake = yearly_spend(float(r["monetary"]), float(r["tenure_days"]))
            raw = str(r["customer"])
            batch.append(
                {
                    "customer_key": pseudonym(key, tenant, raw) if private else raw[:200],
                    "probability": round(p, 6),
                    "risk_band": risk_band(p),
                    "rank_band": TOP_BAND if top[i] else REST_BAND,
                    "revenue_at_stake": round(stake, 4),
                    "expected_loss": round(p * stake, 4),
                    "recency_days": int(r["recency_days"]),
                    "frequency": int(r["frequency"]),
                    "monetary": round(float(r["monetary"]), 4),
                    "reasons": _reasons(fit, fit.x[i], contributions[j], typical, ctx.locale),
                }
            )
        yield batch
