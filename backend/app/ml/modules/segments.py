"""Customer segmentation: RFM segments (SQL) and K-Means clusters with a stability check."""

import hashlib
import hmac
from collections import Counter
from collections.abc import Iterator
from datetime import date
from typing import Any

import numpy as np

from app.ml.base import AnalysisContext, AnalysisModule, DataFacts, Insight
from app.ml.budget import Budget
from app.ml.format import fmt_money, fmt_number, fmt_pct
from app.ml.modules.common import (
    SENSITIVITY,
    as_date,
    build_insight,
    evidence_query,
    excluded_rows,
    refund_rows,
    revenue_expr,
    revenue_key_columns,
)
from app.ml.quality import cap_confidence
from app.ml.segmentation import clusters, rfm
from app.services.profiling.results import confidence_for

MIN_CUSTOMERS = 100
ASSIGNMENT_BATCH = 5000
CACHE_KEY = "segment_assignments"
DESCRIBE_NOT_EXPLAIN = "Segments describe how customers behaved; they do not explain why."


def pseudonym(key: bytes, tenant_id: str, value: str) -> str:
    """Return a stable per-tenant pseudonym for a customer id (HMAC-SHA256, 16 hex chars)."""
    digest = hmac.new(key, f"{tenant_id}|{value}".encode(), hashlib.sha256).hexdigest()
    return f"cust_{digest[:16]}"


class SegmentationModule(AnalysisModule):
    """Group customers by recency, frequency and spend, with a suggested action per group."""

    name = "segmentation"
    title = "Customer segmentation"
    requires = frozenset({"customer_id", "date", "revenue"})

    def data_requirement(self, facts: DataFacts) -> str | None:
        """Explain when there are too few customers to segment."""
        customers = facts.distinct.get("customer_id")
        if customers is not None and customers < MIN_CUSTOMERS:
            return (
                f"only {customers} customers; customer segmentation needs at least {MIN_CUSTOMERS}"
            )
        return None

    def run(self, context: AnalysisContext) -> list[Insight]:
        """Return the RFM and cluster insights and leave the assignments for the service."""
        ctx = context
        rev, d = revenue_expr(ctx), ctx.roles.q("date")
        last = ctx.db.scalar(f"SELECT max({d}) FROM data WHERE {rev} > 0")
        if last is None:
            ctx.warn("No positive purchase has a date; customer segmentation was skipped.")
            return []
        end = as_date(last)
        cte = rfm.customers_cte(ctx, end)
        count = int(ctx.db.scalar(f"{cte} SELECT count(*) FROM segmented") or 0)
        if count < MIN_CUSTOMERS:
            ctx.warn(
                f"Only {count} customers have a positive purchase; customer segmentation needs "
                f"at least {MIN_CUSTOMERS}, so it was skipped."
            )
            return []
        warnings = _warnings(ctx)
        budget = Budget(ctx.settings.segment_max_seconds, float("inf"))
        summary_sql = rfm.summary_sql(ctx, end)
        segment_rows = ctx.db.rows(summary_sql)
        insights = [_rfm_insight(ctx, segment_rows, summary_sql, count, warnings)]
        fit_rows, sampled = _fit_rows(ctx, cte, count)
        features = np.array([[float(r[f]) for f in clusters.FEATURES] for r in fit_rows])
        out_of_time = lambda: budget.exhausted() is not None  # noqa: E731
        fitted = clusters.fit(features, ctx.settings.ml_seed, out_of_time)
        stable = clusters.stability(
            features,
            fitted,
            ctx.settings.segment_bootstrap_rounds,
            ctx.settings.ml_seed,
            out_of_time,
        )
        cluster_names = clusters.names(features, fitted.labels, fitted.k)
        profiles = _profiles(fit_rows, features, fitted, cluster_names)
        insights.append(
            _cluster_insight(
                ctx, end, profiles, fitted, stable, count, len(fit_rows), sampled, warnings
            )
        )
        ctx.cache[CACHE_KEY] = lambda: _assignments(ctx, cte, fitted, cluster_names)
        return insights


def _warnings(ctx: AnalysisContext) -> list[str]:
    warnings = []
    refunds = refund_rows(ctx)
    if refunds:
        warnings.append(
            f"{fmt_number(refunds, ctx.locale)} refund rows were left out: only positive "
            "purchases count towards recency, frequency and spend."
        )
    if "customer_id" in ctx.facts.pii_roles:
        warnings.append(
            "The customer id column looks like personal data, so customers are listed under "
            "pseudonyms, never their raw ids."
        )
    return warnings


def _fit_rows(ctx: AnalysisContext, cte: str, count: int) -> tuple[list[dict[str, Any]], bool]:
    """Fetch the customers used to fit (all, or a deterministic sample of SEGMENT_MAX_CUSTOMERS)."""
    limit = ctx.settings.segment_max_customers
    sampled = count > limit
    order = (
        f"ORDER BY md5(CAST(customer AS VARCHAR) || '{ctx.settings.ml_seed}') LIMIT {limit}"
        if sampled
        else "ORDER BY customer"
    )
    rows = ctx.db.rows(
        f"{cte} SELECT customer, segment, recency_days, frequency, monetary, "
        f"monetary / frequency AS aov, tenure_days FROM segmented {order}"
    )
    if sampled:
        ctx.warn(
            f"{fmt_number(count, ctx.locale)} customers: the clusters were fitted on a sample of "
            f"{fmt_number(limit, ctx.locale)} (SEGMENT_MAX_CUSTOMERS) and every customer was then "
            "assigned to the nearest one."
        )
    return rows, sampled


def _profiles(
    rows: list[dict[str, Any]],
    features: np.ndarray,
    fitted: clusters.Clustering,
    cluster_names: list[str],
) -> list[dict[str, Any]]:
    total = float(features[:, 2].sum()) or 1.0
    out = []
    for c in range(fitted.k):
        mask = fitted.labels == c
        member = features[mask]
        segments = Counter(r["segment"] for r, m in zip(rows, mask, strict=True) if m)
        dominant = sorted(segments.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        out.append(
            {
                "cluster": c,
                "name": cluster_names[c],
                "customers": int(mask.sum()),
                "share_pct": round(100.0 * mask.sum() / len(rows), 2),
                "revenue_share_pct": round(100.0 * float(member[:, 2].sum()) / total, 2),
                "avg_recency_days": round(float(member[:, 0].mean()), 1),
                "avg_orders": round(float(member[:, 1].mean()), 2),
                "avg_spend": round(float(member[:, 2].mean()), 2),
                "median_spend": round(float(np.median(member[:, 2])), 2),
                "avg_order_value": round(float(member[:, 3].mean()), 2),
                "avg_tenure_days": round(float(member[:, 4].mean()), 1),
                "dominant_rfm_segment": dominant,
                "recommended_action": rfm.suggestion(dominant),
            }
        )
    return out


def _rfm_insight(
    ctx: AnalysisContext,
    rows: list[dict[str, Any]],
    sql: str,
    customers: int,
    warnings: list[str],
) -> Insight:
    loc, cur = ctx.locale, ctx.currency
    segments = [
        {
            "segment": r["segment"],
            "customers": int(r["customers"]),
            "revenue_share_pct": float(r["revenue_share_pct"]),
            "revenue": float(r["revenue"]),
            "avg_recency_days": float(r["avg_recency_days"]),
            "avg_orders": float(r["avg_orders"]),
            "avg_spend": float(r["avg_spend"]),
            "recommended_action": rfm.suggestion(str(r["segment"])),
        }
        for r in rows
    ]
    by_name = {s["segment"]: s for s in segments}
    at_risk = [by_name[n] for n in ("At risk", "Hibernating") if n in by_name]
    yearly = {str(r["segment"]): float(r["yearly_spend"]) for r in rows}
    money = sum(yearly.get(s["segment"], 0.0) for s in at_risk)
    top = segments[0]
    risk_text = (
        f" {fmt_number(sum(s['customers'] for s in at_risk), loc)} customers are At risk or "
        f"Hibernating; they used to spend about {fmt_money(money, cur, loc)} a year. "
        f"{rfm.suggestion('At risk')['text']}"
        if at_risk
        else ""
    )
    summary = (
        f"{fmt_number(customers, loc)} customers fall into {len(segments)} RFM segments. "
        f"{top['segment']} bring the most revenue: {fmt_pct(top['revenue_share_pct'], loc)} "
        f"from {fmt_number(top['customers'], loc)} customers."
        + risk_text
        + f" {DESCRIBE_NOT_EXPLAIN}"
    )
    keys = [ctx.roles.columns["customer_id"], ctx.roles.columns["date"], *revenue_key_columns(ctx)]
    return build_insight(
        ctx,
        module="segmentation",
        type="rfm_segments",
        title=(
            f"{top['segment']} customers bring {fmt_pct(top['revenue_share_pct'], loc)} of revenue"
        ),
        summary=summary,
        value={s["segment"]: s["customers"] for s in segments},
        sample_size=customers,
        method=(
            "RFM in SQL over positive purchases: recency = days since the last purchase, "
            "frequency = orders, monetary = spend; scores 1-5 from rank quintiles (ties share a "
            "score); segment from the R x F table in app/ml/segmentation/rfm.py"
        ),
        key_columns=keys,
        money_effect=money,
        payload={
            "segments": segments,
            "segment_map": {f"R{r}F{f}": n for (r, f), n in sorted(rfm.SEGMENT_MAP.items())},
            "revenue_at_risk_per_year": round(money, 2),
            "pseudonymized_customer_ids": "customer_id" in ctx.facts.pii_roles,
            "suggestions_are_rule_based": True,
        },
        chart_spec={
            "type": "bar",
            "title": "Customers and revenue share by RFM segment",
            "x": "segment",
            "y": ["customers", "revenue_share_pct"],
            "data": "payload.segments",
        },
        evidence=[evidence_query("Customers, revenue and averages per RFM segment", sql, rows)],
        excluded=excluded_rows(ctx, keys),
        warnings=tuple(warnings),
        what="segments",
    )


def _cluster_insight(
    ctx: AnalysisContext,
    end: date,
    profiles: list[dict[str, Any]],
    fitted: clusters.Clustering,
    stable: clusters.Stability,
    customers: int,
    fitted_on: int,
    sampled: bool,
    warnings: list[str],
) -> Insight:
    loc = ctx.locale
    sql = rfm.features_sql(ctx, end)
    feature_rows = ctx.db.rows(sql)
    level = cap_confidence(stable.confidence, confidence_for(customers))
    stability_text = (
        f"resampling the customers {stable.rounds} times gives a median adjusted Rand index of "
        f"{fmt_number(stable.median_ari, loc, 2)} (1 = identical groups)"
        if stable.median_ari is not None
        else "the stability check could not run within the time limit"
    )
    parts = [
        f"{p['name']} ({fmt_number(p['customers'], loc)} customers, "
        f"{fmt_pct(p['revenue_share_pct'], loc)} of revenue)"
        for p in profiles
    ]
    summary = (
        f"K-Means finds {fitted.k} customer groups: {'; '.join(parts)}. "
        f"The groups are {'stable' if stable.confidence != 'low' else 'not very stable'}: "
        f"{stability_text}. Each group has a rule-based suggestion for what to do next. "
        f"{DESCRIBE_NOT_EXPLAIN}"
    )
    keys = [ctx.roles.columns["customer_id"], ctx.roles.columns["date"], *revenue_key_columns(ctx)]
    all_warnings = [*warnings, *fitted.warnings]
    if stable.rounds < ctx.settings.segment_bootstrap_rounds:
        all_warnings.append(
            f"Only {stable.rounds} of {ctx.settings.segment_bootstrap_rounds} stability rounds "
            "ran before the time limit."
        )
    total_spend = sum(p["avg_spend"] * p["customers"] for p in profiles)
    return build_insight(
        ctx,
        module="segmentation",
        type="customer_clusters",
        title=f"{fitted.k} customer groups found",
        summary=summary,
        value=fitted.k,
        sample_size=customers,
        method=(
            "K-Means (scikit-learn, 10 starts, fixed seed) on standardized log1p of recency, "
            "frequency, spend, average order value and tenure; k from 3 to 8 by silhouette score; "
            f"stability = median adjusted Rand index over {stable.rounds} bootstrap refits"
        ),
        key_columns=keys,
        money_effect=SENSITIVITY * total_spend,
        payload={
            "k": fitted.k,
            "silhouette": {str(k): round(v, 4) for k, v in fitted.silhouette.items()},
            "stability": {
                "median_ari": stable.median_ari,
                "p10_ari": stable.p10_ari,
                "rounds": stable.rounds,
            },
            "clusters": profiles,
            "features": list(clusters.FEATURES),
            "fitted_on_customers": fitted_on,
            "sampled": sampled,
            "suggestions_are_rule_based": True,
        },
        chart_spec={
            "type": "bar",
            "title": "Customer groups: size and revenue share",
            "x": "name",
            "y": ["customers", "revenue_share_pct"],
            "data": "payload.clusters",
        },
        evidence=[
            evidence_query(
                "Clustering inputs per customer (averages and medians)", sql, feature_rows
            )
        ],
        excluded=excluded_rows(ctx, keys),
        confidence=level,  # type: ignore[arg-type]
        warnings=tuple(all_warnings),
        what="segments",
    )


def _assignments(
    ctx: AnalysisContext, cte: str, fitted: clusters.Clustering, cluster_names: list[str]
) -> Iterator[list[dict[str, Any]]]:
    """Yield batches of per-customer rows for the segment_assignments table."""
    private = "customer_id" in ctx.facts.pii_roles
    key = ctx.settings.pseudonym_key
    tenant = str(ctx.tenant_id)
    sql = (
        f"{cte} SELECT customer, segment, r_score, f_score, m_score, recency_days, frequency, "
        f"monetary, monetary / frequency AS aov, tenure_days FROM segmented ORDER BY customer"
    )
    for batch in ctx.db.batches(sql, ASSIGNMENT_BATCH):
        features = np.array([[float(r[f]) for f in clusters.FEATURES] for r in batch])
        labels = fitted.predict(features)
        yield [
            {
                "customer_key": (
                    pseudonym(key, tenant, str(r["customer"]))
                    if private
                    else str(r["customer"])[:200]
                ),
                "rfm_segment": r["segment"],
                "r_score": int(r["r_score"]),
                "f_score": int(r["f_score"]),
                "m_score": int(r["m_score"]),
                "recency_days": int(r["recency_days"]),
                "frequency": int(r["frequency"]),
                "monetary": r["monetary"],
                "cluster": int(label),
                "cluster_name": cluster_names[int(label)],
            }
            for r, label in zip(batch, labels, strict=True)
        ]
