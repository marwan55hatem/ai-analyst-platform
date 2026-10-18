"""Forecast: net revenue (or quantity) for the whole business and its top categories/products."""

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.ml.base import AnalysisContext, AnalysisModule, Confidence, Insight
from app.ml.forecasting.budget import Budget, reason_text
from app.ml.forecasting.engine import ForecastResult, forecast_series
from app.ml.forecasting.series import (
    FREQS,
    Freq,
    PreparedSeries,
    choose_frequency,
    history_sql,
    prepare_series,
)
from app.ml.forecasting.text import forecast_text, metric_label
from app.ml.forecasting.tracking import log_forecast_run
from app.ml.modules.common import (
    as_date,
    build_insight,
    evidence_query,
    excluded_rows,
    revenue_expr,
    revenue_key_columns,
    revenue_note,
)
from app.ml.quality import cap_confidence
from app.ml.roles import DIMENSION_ROLES, RoleMap
from app.services.profiling.parsing import quote_ident

DEFAULT_HORIZON = 6
AUTO_GROUPS = 5
METRICS = ("revenue", "quantity", "cost")
CHART_HISTORY_PERIODS = 36


def metric_expression(ctx: AnalysisContext, metric: str) -> str:
    """Return the SQL expression that is summed per period for a metric."""
    if metric == "revenue":
        return revenue_expr(ctx)
    return ctx.roles.q(metric)


def resolve_group_column(roles: RoleMap, group_by: str) -> str:
    """Return the column behind a group_by value (a dimension role or its column name)."""
    if group_by in DIMENSION_ROLES and group_by in roles.columns:
        return roles.columns[group_by]
    for role in DIMENSION_ROLES:
        if roles.columns.get(role) == group_by:
            return group_by
    raise ValueError(
        f"'{group_by}' is not a dimension column; use one of "
        f"{', '.join(r for r in DIMENSION_ROLES if r in roles.columns) or 'none available'}"
    )


@dataclass
class _Plan:
    metric: str
    horizon: int
    group_col: str | None
    max_groups: int


class ForecastModule(AnalysisModule):
    """Backtested forecasts with 80% and 95% intervals, honest about weak results."""

    name = "forecast"
    title = "Forecast"
    requires = frozenset({"date"})
    requires_any = frozenset({"revenue", "quantity"})

    def notes(self, roles: RoleMap) -> list[str]:
        """Explain what will not be forecast."""
        if not any(r in roles.columns for r in ("category", "product")):
            return ["No category or product column, so only the overall total is forecast."]
        return []

    def run(self, context: AnalysisContext) -> list[Insight]:
        """Forecast the total and the top groups; one insight each."""
        plan = _plan(context)
        if plan is None:
            return []
        rev, d = metric_expression(context, plan.metric), context.roles.q("date")
        bounds = context.db.one(
            f"SELECT min({d}) AS first, max({d}) AS last FROM data "
            f"WHERE {rev} IS NOT NULL AND {d} IS NOT NULL"
        )
        if bounds.get("last") is None:
            context.warn("There are no dated values to forecast.")
            return []
        first, last = as_date(bounds["first"]), as_date(bounds["last"])
        override = context.config.get("frequency")
        freq = (
            FREQS[override]
            if override in FREQS
            else choose_frequency(context.granularity, first, last)
        )
        if freq is None:
            context.warn("Yearly data has too few periods to forecast; the forecast was skipped.")
            return []
        settings = context.settings
        budget = Budget(settings.forecast_max_seconds, settings.forecast_memory_mb)
        seed = settings.forecast_seed
        series: list[tuple[str | None, PreparedSeries, str, list[dict[str, Any]]]] = []
        total_sql = history_sql(rev, d, freq)
        total_rows = context.db.rows(total_sql)
        total = prepare_series("Total", total_rows, freq, last)
        if total is None:
            context.warn(
                f"Fewer than 3 complete {freq.plural} of history, so no forecast was made."
            )
            return []
        series.append((None, total, total_sql, total_rows))
        series.extend(_group_series(context, plan, rev, d, freq, last))

        results, skipped_groups = _forecast_all(series, plan.horizon, budget, seed, context)
        if skipped_groups:
            context.warn(
                f"Because {reason_text(budget.exhausted() or 'time', budget)}, "
                f"{len(skipped_groups)} group forecast(s) were skipped: "
                f"{', '.join(skipped_groups[:5])}."
            )
        budget.rss_mb()  # records the peak for the payload and MLflow
        context.cache["forecast_resources"] = {
            "peak_rss_mb": round(budget.peak_rss_mb, 1),
            "elapsed_seconds": round(budget.elapsed(), 1),
            "memory_limit_mb": settings.forecast_memory_mb,
            "time_limit_seconds": settings.forecast_max_seconds,
        }
        outcome = log_forecast_run(
            settings.mlflow_tracking_uri,
            context.tenant_id,
            context.version_id,
            {**_params(plan, freq, seed, settings, context), **context.cache["forecast_resources"]},
            [r for _, r, _ in results],
        )
        if outcome.warning:
            context.warn(outcome.warning)
        return [
            _insight(
                context, plan, group, result, sql, rows, prep, outcome.run_id, outcome.experiment
            )
            for group, result, sql, rows, prep in _flatten(results, series)
        ]


def _plan(ctx: AnalysisContext) -> _Plan | None:
    settings = ctx.settings
    metric = str(
        ctx.config.get("metric") or ("revenue" if ctx.roles.has("revenue") else "quantity")
    )
    if metric not in METRICS or not (ctx.roles.has(metric)):
        ctx.warn(f"The metric '{metric}' is not available in this dataset.")
        return None
    horizon = int(ctx.config.get("horizon") or DEFAULT_HORIZON)
    horizon = max(1, min(horizon, settings.forecast_max_horizon))
    group_by = ctx.config.get("group_by")
    group_col: str | None = None
    if group_by:
        group_col = resolve_group_column(ctx.roles, str(group_by))
    elif not ctx.config.get("forecast_custom"):
        group_col = next(
            (ctx.roles.columns[r] for r in ("category", "product") if r in ctx.roles.columns), None
        )
    max_groups = int(ctx.config.get("max_groups") or AUTO_GROUPS)
    max_groups = max(
        0, min(max_groups, settings.forecast_max_groups, settings.forecast_max_series - 1)
    )
    return _Plan(metric, horizon, group_col, max_groups)


def _group_series(
    ctx: AnalysisContext, plan: _Plan, rev: str, d: str, freq: Freq, last: date
) -> list[tuple[str | None, PreparedSeries, str, list[dict[str, Any]]]]:
    if plan.group_col is None or plan.max_groups == 0:
        return []
    g = quote_ident(plan.group_col)
    top = ctx.db.rows(
        f"SELECT {g} AS grp, sum({rev}) AS value FROM data WHERE {rev} IS NOT NULL "
        f"AND {d} IS NOT NULL AND {g} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 "
        f"LIMIT {plan.max_groups}"
    )
    out: list[tuple[str | None, PreparedSeries, str, list[dict[str, Any]]]] = []
    for row in top:
        name = str(row["grp"])
        sql = history_sql(rev, d, freq, g, [name])
        rows = ctx.db.rows(sql)
        prep = prepare_series(name, [r for r in rows], freq, last)
        if prep is None:
            ctx.warn(f"'{name}' has fewer than 3 complete {freq.plural} of history; skipped.")
            continue
        out.append((name, prep, sql, rows))
    return out


def _forecast_all(
    series: list[tuple[str | None, PreparedSeries, str, list[dict[str, Any]]]],
    horizon: int,
    budget: Budget,
    seed: int,
    ctx: AnalysisContext,
) -> tuple[list[tuple[str | None, ForecastResult, int]], list[str]]:
    results: list[tuple[str | None, ForecastResult, int]] = []
    skipped: list[str] = []
    for i, (group, prep, _sql, _rows) in enumerate(series):
        if i > 0 and budget.exhausted():
            skipped.extend(str(g) for g, *_ in series[i:])
            break
        results.append((group, forecast_series(prep, horizon, budget, seed), i))
        budget.release()
    return results, skipped


def _flatten(
    results: list[tuple[str | None, ForecastResult, int]],
    series: list[tuple[str | None, PreparedSeries, str, list[dict[str, Any]]]],
) -> list[tuple[str | None, ForecastResult, str, list[dict[str, Any]], PreparedSeries]]:
    return [(g, r, series[i][2], series[i][3], series[i][1]) for g, r, i in results]


def _params(
    plan: _Plan, freq: Freq, seed: int, settings: Any, ctx: AnalysisContext
) -> dict[str, Any]:
    return {
        "metric": plan.metric,
        "horizon": plan.horizon,
        "frequency": freq.key,
        "season_length": freq.season,
        "group_by_role": "none" if plan.group_col is None else "dimension",
        "max_groups": plan.max_groups,
        "seed": seed,
        "max_seconds": settings.forecast_max_seconds,
        "memory_limit_mb": settings.forecast_memory_mb,
        "granularity": ctx.granularity or "unknown",
        "candidates": "SeasonalNaive,AutoETS,AutoARIMA,AutoTheta,LightGBM",
    }


def _insight(
    ctx: AnalysisContext,
    plan: _Plan,
    group: str | None,
    result: ForecastResult,
    sql: str,
    rows: list[dict[str, Any]],
    prep: PreparedSeries,
    mlflow_run: str | None,
    experiment: str | None,
) -> Insight:
    keys = [*revenue_key_columns(ctx), ctx.roles.columns["date"]]
    if plan.metric != "revenue":
        keys = [ctx.roles.columns[plan.metric], ctx.roles.columns["date"]]
    excluded = excluded_rows(ctx, keys)
    caveats = ctx.quality.caveats(keys, excluded, "figures")
    level: Confidence = cap_confidence(result.confidence, caveats.cap)  # type: ignore[assignment]
    title, summary = forecast_text(
        result,
        metric=plan.metric,
        confidence=level,
        currency=ctx.currency,
        locale=ctx.locale,
        group=group,
    )
    horizon_n = len(result.point)
    recent = sum(result.history[-horizon_n:])
    effect = (sum(result.point) - recent) if plan.metric != "quantity" else 0.0
    notes = [revenue_note(ctx)] if plan.metric == "revenue" and revenue_note(ctx) else []
    data = {
        "series": result.series,
        "group": group,
        "metric": plan.metric,
        "model": result.model,
        "frequency": result.freq_key,
        "horizon": horizon_n,
        "periods": [p.isoformat() for p in result.periods],
        "point": result.point,
        "lo80": result.lo80,
        "hi80": result.hi80,
        "lo95": result.lo95,
        "hi95": result.hi95,
        "scores": [s.as_dict() for s in result.scores],
        "baseline": result.baseline_score.as_dict() if result.baseline_score else None,
        "winner": result.winner_score.as_dict() if result.winner_score else None,
        "no_better_than_baseline": result.no_better_than_baseline,
        "confidence_reasons": result.confidence_reasons,
        "skipped_models": result.skipped,
        "history_cycles": round(result.cycles, 2),
        "backtest": {"horizon": result.backtest_horizon, "windows": result.backtest_windows},
        "seed": result.seed,
        "net_revenue_basis": "net",
        "mlflow": {"run_id": mlflow_run, "experiment": experiment},
        "resources": ctx.cache.get("forecast_resources", {}),
    }
    suffix = f" - {group}" if group else ""
    chart = {
        "type": "forecast_band",
        "title": f"{metric_label(plan.metric)}{suffix}: history and forecast",
        "unit": result.freq_key,
        "history": [
            {"period": p.isoformat(), "value": round(v, 2)}
            for p, v in zip(result.history_periods, result.history, strict=True)
        ][-CHART_HISTORY_PERIODS:],
        "forecast": [
            {
                "period": p.isoformat(),
                "point": result.point[i],
                "lo80": result.lo80[i],
                "hi80": result.hi80[i],
                "lo95": result.lo95[i],
                "hi95": result.hi95[i],
            }
            for i, p in enumerate(result.periods)
        ],
    }
    purpose = f"{metric_label(plan.metric)} per {result.freq_key}" + (
        f" for {group}" if group else ""
    )
    return build_insight(
        ctx,
        module="forecast",
        type="forecast_group" if group else "forecast_total",
        title=title,
        summary=summary,
        value=result.value,
        sample_size=result.sample_size,
        method=result.method,
        key_columns=keys,
        money_effect=effect,
        payload=data,
        chart_spec=chart,
        evidence=[evidence_query(purpose, sql, rows)],
        excluded=excluded,
        confidence=level,
        warnings=tuple(notes + result.warnings),
        what="figures",
    )
