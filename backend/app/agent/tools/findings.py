"""Tools over the stored results of the analysis run, plus forecasts and what-if scenarios."""

import json
import time
from typing import Any, Literal

from pydantic import Field

from app.agent.tools.base import (
    ENVELOPE_CHARS,
    AgentContext,
    Tool,
    ToolArgs,
    ToolResult,
    content_size,
    fit_rows,
)
from app.core.errors import AppError
from app.db.repository import AnalysisRunRepository, InsightRepository, ModelRunRepository
from app.models.analysis import AnalysisRun, Insight
from app.schemas.analysis import ChurnScoreOut, SegmentAssignmentOut
from app.schemas.forecast import ForecastRequest
from app.schemas.whatif import ScenarioIn, WhatIfRequest
from app.services import findings
from app.services.forecasting import start_forecast
from app.services.whatif import run_what_if

FORECAST_WAIT_S = 60.0
FORECAST_POLL_S = 1.0
PAYLOAD_CHARS = 1_500
BRIEF_KEYS = ("module", "type", "title", "summary", "confidence", "sample_size")
TITLE_KEYS = ("type", "title", "confidence", "sample_size")
SHORTENED_NOTE = "Shortened to show every insight; call again with `type` for one type's detail."
CHURN_COUNTS_NOTE = (
    "customers.total is everyone scored, so report that as the sample size. The counts under "
    "model.models are only the held-out customers used to check the model."
)
ITEM_SCHEMAS: dict[str, Any] = {"customers": ChurnScoreOut, "assignments": SegmentAssignmentOut}
DROPPED_KEYS = {"evidence", "mlflow", "resources", "seed", "scores", "skipped_models"}


class NoArgs(ToolArgs):
    """The tool takes no arguments."""


class InsightArgs(ToolArgs):
    """Filters for stored insights."""

    type: str | None = Field(default=None, max_length=50, description="Insight type filter.")
    module: str | None = Field(default=None, max_length=50, description="Module filter.")
    limit: int = Field(default=20, ge=1, le=20)


class ForecastArgs(ToolArgs):
    """Which metric to forecast and how far."""

    metric: Literal["revenue", "quantity", "cost"] = "revenue"
    horizon: int = Field(default=6, ge=1, le=24, description="Periods ahead.")


class SegmentArgs(ToolArgs):
    """How many customers of the assignment list to include."""

    limit: int = Field(default=10, ge=1, le=20)


class ChurnArgs(ToolArgs):
    """How many of the riskiest customers to list."""

    top_n: int = Field(default=10, ge=1, le=50)


class WhatIfArgs(ToolArgs):
    """One to three lever changes."""

    scenarios: list[ScenarioIn] = Field(min_length=1, max_length=3)


def _latest_run(ctx: AgentContext) -> AnalysisRun | None:
    return AnalysisRunRepository(ctx.db, ctx.tenant_id).latest_for_version(ctx.version.id)


def _no_run() -> ToolResult:
    return ToolResult.error(
        "No analysis has run on this dataset version yet, so there are no stored results. "
        "Use run_sql, or ask the user to start an analysis.",
        code="no_analysis_run",
    )


def _trim(payload: dict[str, Any]) -> dict[str, Any]:
    kept = {k: v for k, v in payload.items() if k not in DROPPED_KEYS}
    text = json.dumps(kept, default=str)
    if len(text) <= PAYLOAD_CHARS:
        return kept
    small = {k: v for k, v in kept.items() if len(json.dumps(v, default=str)) <= 300}
    small["_note"] = "large fields were left out"
    return small


def _insight(insight: Insight) -> dict[str, Any]:
    return {
        "id": str(insight.id),
        "module": insight.module,
        "type": insight.type,
        "title": insight.title,
        "summary": insight.summary,
        "confidence": insight.confidence,
        "sample_size": insight.sample_size,
        "data": _trim(insight.payload or {}),
    }


def fit_insights(views: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], bool]:
    """Shrink insights to fit `limit` characters; return them and whether they were shortened.

    Cutting the list at the character cap would hide whatever ranks last, so every insight stays
    and loses detail instead: first its payload, then its summary.
    """
    room = limit - ENVELOPE_CHARS
    if content_size(views) <= room:
        return views, False
    brief = [{k: view[k] for k in BRIEF_KEYS} for view in views]
    if content_size(brief) <= room:
        return brief, True
    return [{k: view[k] for k in TITLE_KEYS} for view in views], True


def _insight_evidence(insights: list[Insight]) -> list[dict[str, Any]]:
    return [{"insight_id": str(i.id), "type": i.type, "evidence": i.evidence} for i in insights]


def get_insights(ctx: AgentContext, args: InsightArgs) -> ToolResult:
    """Return stored insights of the latest run, highest impact first."""
    run = _latest_run(ctx)
    if run is None:
        return _no_run()
    insights = InsightRepository(ctx.db, ctx.tenant_id).for_run(
        run.id, type=args.type, module=args.module, limit=args.limit
    )
    views, shortened = fit_insights(
        [_insight(i) for i in insights], ctx.settings.agent_tool_output_chars
    )
    content: dict[str, Any] = {
        "run_id": str(run.id),
        "run_status": run.status,
        "insights": views,
    }
    if shortened:
        content["note"] = SHORTENED_NOTE
    return ToolResult(
        ok=True,
        content=content,
        evidence={"run_id": str(run.id), "insights": _insight_evidence(insights)},
        summary=f"{len(insights)} insights",
    )


def get_forecast(ctx: AgentContext, args: ForecastArgs) -> ToolResult:
    """Return the stored forecast if it covers the request, else run a job (60 s wait max)."""
    run = _latest_run(ctx)
    stored: list[Insight] = []
    if run is not None:
        stored = [
            i
            for i in InsightRepository(ctx.db, ctx.tenant_id).for_run(
                run.id, module="forecast", limit=20
            )
            if i.payload.get("metric") == args.metric
            and int(i.payload.get("horizon") or 0) >= args.horizon
        ]
    if stored:
        return _forecast_result(
            [
                _forecast_view(i.title, i.summary, i.confidence, i.payload, args.horizon)
                for i in stored
            ],
            "stored analysis run",
            _insight_evidence(stored),
            ctx.settings.agent_tool_output_chars,
        )
    return _custom_forecast(ctx, args)


def _custom_forecast(ctx: AgentContext, args: ForecastArgs) -> ToolResult:
    try:
        model_run = start_forecast(
            ctx.db,
            ctx.tenant_id,
            ctx.version,
            ForecastRequest(metric=args.metric, horizon=args.horizon),
        )
    except AppError as exc:
        return ToolResult.error(exc.message, code=exc.code)
    runs = ModelRunRepository(ctx.db, ctx.tenant_id)
    deadline = time.monotonic() + FORECAST_WAIT_S
    while True:
        ctx.db.expire_all()
        current = runs.get(model_run.id)
        if current is None or current.status in ("completed", "failed"):
            break
        if time.monotonic() > deadline:
            return ToolResult.error(
                f"The forecast is still running (model run {model_run.id}); ask again shortly.",
                code="forecast_running",
            )
        time.sleep(FORECAST_POLL_S)
    if current is None or current.status == "failed":
        return ToolResult.error("The forecast failed.", code="forecast_failed")
    items = current.result.get("insights", [])
    views = [
        _forecast_view(i["title"], i["summary"], i["confidence"], i["payload"], args.horizon)
        for i in items
    ]
    return _forecast_result(
        views,
        f"forecast job {current.id}",
        [{"model_run_id": str(current.id)}],
        ctx.settings.agent_tool_output_chars,
    )


def _forecast_view(
    title: str, summary: str, confidence: str, payload: dict[str, Any], horizon: int
) -> dict[str, Any]:
    keys = ("periods", "point", "lo80", "hi80", "lo95", "hi95")
    view: dict[str, Any] = {k: (payload.get(k) or [])[:horizon] for k in keys}
    view.update(
        title=title,
        summary=summary,
        confidence=confidence,
        series=payload.get("series"),
        metric=payload.get("metric"),
        model=payload.get("model"),
        frequency=payload.get("frequency"),
        winner=payload.get("winner"),
        baseline=payload.get("baseline"),
        no_better_than_baseline=payload.get("no_better_than_baseline"),
    )
    return view


def _forecast_result(
    views: list[dict[str, Any]], source: str, evidence: list[dict[str, Any]], limit: int
) -> ToolResult:
    shown = list(views)  # the total comes first, so it is the last series to be dropped
    content = {"source": source, "interval_levels_pct": [80, 95], "forecasts": shown}
    cut = fit_rows(content, shown, limit)
    if cut:
        content["note"] = cut
    return ToolResult(
        ok=True,
        content=content,
        evidence={"source": source, "items": evidence, "forecasts": views},
        summary=f"{len(views)} forecast series from the {source}",
    )


def _stored_findings(
    ctx: AgentContext, build: Any, keep: tuple[str, ...], **kwargs: Any
) -> ToolResult:
    run = _latest_run(ctx)
    if run is None:
        return _no_run()
    payload = build(ctx.db, ctx.tenant_id, run.id, **kwargs)
    for key, schema in ITEM_SCHEMAS.items():  # per-customer rows are ORM objects
        block = payload.get(key)
        if isinstance(block, dict) and block.get("items"):
            block["items"] = [
                schema.model_validate(i).model_dump(mode="json") for i in block["items"]
            ]
    insights: list[Insight] = payload.pop("insights", [])
    content = {k: payload.get(k) for k in keep}
    content["run_id"] = str(run.id)
    content["insights"] = [
        {"title": i.title, "summary": i.summary, "confidence": i.confidence, "type": i.type}
        for i in insights[:5]
    ]
    if payload.get("module_status") not in (None, "completed", "ok"):
        content["note"] = payload.get("skipped_reason") or "the module did not complete"
    content = json.loads(json.dumps(content, default=str))
    limit = ctx.settings.agent_tool_output_chars
    if content_size(content) > limit - ENVELOPE_CHARS:
        compact_findings(content)
    rows = _row_list(content)
    if rows:
        cut = fit_rows(content, rows, limit)
        if cut:
            content["rows_note"] = cut
    return ToolResult(
        ok=True,
        content=content,
        evidence={"run_id": str(run.id), "insights": _insight_evidence(insights)},
        summary=f"{len(insights)} insights",
    )


def compact_findings(content: dict[str, Any]) -> None:
    """Keep what the model can use from an oversized result, so more customers fit.

    Reasons stay as their sentence (which holds the numbers); SHAP values are for the evidence.
    """
    content["insights"] = [
        {"title": i["title"], "confidence": i["confidence"]} for i in content.get("insights", [])
    ]
    if isinstance(content.get("importance"), list):
        content["importance"] = content["importance"][:3]
    for row in _row_list(content) or []:
        if isinstance(row, dict) and isinstance(row.get("reasons"), list):
            row["reasons"] = [r["text"] for r in row["reasons"] if isinstance(r, dict)]


def _row_list(content: dict[str, Any]) -> list[Any] | None:
    """Return the list of per-customer or per-event rows in a findings result, if there is one."""
    for key in ITEM_SCHEMAS:
        block = content.get(key)
        if isinstance(block, dict) and isinstance(block.get("items"), list):
            items: list[Any] = block["items"]
            return items
    events = content.get("events")
    return events if isinstance(events, list) else None


def get_anomalies(ctx: AgentContext, _: NoArgs) -> ToolResult:
    """Unusual days or weeks with likely contributors, and level shifts."""
    return _stored_findings(
        ctx,
        findings.anomalies_payload,
        ("module_status", "skipped_reason", "unit", "events", "level_shifts", "warnings"),
    )


def get_segments(ctx: AgentContext, args: SegmentArgs) -> ToolResult:
    """RFM segments and K-Means groups (pseudonyms when customer ids are personal data)."""
    return _stored_findings(
        ctx,
        findings.segments_payload,
        (
            "module_status",
            "skipped_reason",
            "segments",
            "clusters",
            "k",
            "stability",
            "pseudonymized_customer_ids",
            "warnings",
        ),
        segment=None,
        cluster=None,
        limit=args.limit,
        offset=0,
    )


def get_churn_risk(ctx: AgentContext, args: ChurnArgs) -> ToolResult:
    """Churn model quality, risk bands and the riskiest customers."""
    result = _stored_findings(
        ctx,
        findings.churn_payload,
        (
            "module_status",
            "skipped_reason",
            "basis",
            "bands",
            "importance",
            "model",
            "customers",
            "pseudonymized_customer_ids",
            "warnings",
        ),
        risk_band=None,
        rank_band=None,
        limit=args.top_n,
        offset=0,
    )
    if result.ok and result.content.get("model"):
        result.content["counts_note"] = CHURN_COUNTS_NOTE
    return result


def explain_drivers(ctx: AgentContext, _: NoArgs) -> ToolResult:
    """Which factors go together with revenue (associations, not causes)."""
    return _stored_findings(
        ctx,
        findings.drivers_payload,
        (
            "module_status",
            "skipped_reason",
            "importance",
            "r2_holdout",
            "methods_agree_on_top",
            "association_not_causation",
            "warnings",
        ),
    )


def what_if(ctx: AgentContext, args: WhatIfArgs) -> ToolResult:
    """Yearly revenue and profit change ranges for up to three lever changes."""
    try:
        result = run_what_if(
            ctx.db, ctx.tenant_id, ctx.version, WhatIfRequest(scenarios=args.scenarios)
        )
    except AppError as exc:
        return ToolResult.error(exc.message, code=exc.code)
    scenarios = json.loads(json.dumps(result["scenarios"], default=str))
    content = {
        "note": result["note"],
        "scenarios": [
            {k: v for k, v in s.items() if k not in ("evidence", "estimates_tried")}
            for s in scenarios
        ],
    }
    return ToolResult(
        ok=True,
        content=content,
        evidence={"scenarios": scenarios},
        summary=f"{len(scenarios)} scenarios",
    )


TOOLS = [
    Tool(
        "get_insights",
        "Stored insights of the latest analysis run, highest impact first; many at once come back "
        "shortened. Filter by `type` for full detail: kpi_summary, growth, returns, pareto, "
        "dead_stock, seasonality, weekday_pattern, growth_extremes, ytd_vs_prior_year, "
        "cohort_retention, forecast_total, forecast_group, anomaly, rfm_segments, "
        "customer_clusters, churn_risk, revenue_drivers.",
        InsightArgs,
        get_insights,
    ),
    Tool(
        "get_forecast",
        "Forecast of revenue, quantity or cost with 80% and 95% ranges, the winning model and "
        "whether it beat the seasonal-naive baseline.",
        ForecastArgs,
        get_forecast,
    ),
    Tool(
        "get_anomalies",
        "Unusual periods, their likely contributors and level shifts.",
        NoArgs,
        get_anomalies,
    ),
    Tool(
        "get_segments",
        "Customer RFM segments and K-Means groups with suggested actions.",
        SegmentArgs,
        get_segments,
    ),
    Tool(
        "get_churn_risk",
        "Churn model quality, risk bands and the riskiest customers.",
        ChurnArgs,
        get_churn_risk,
    ),
    Tool(
        "explain_drivers",
        "Factors associated with revenue (SHAP), associations not causes.",
        NoArgs,
        explain_drivers,
    ),
    Tool(
        "what_if",
        "Estimate the yearly revenue and profit change of price, discount, volume or cost "
        "changes (±30% max) with low/expected/high ranges and assumptions.",
        WhatIfArgs,
        what_if,
    ),
]
