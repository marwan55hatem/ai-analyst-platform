"""Report facts: everything a report prints, read from stored analysis results only.

The report layer estimates nothing. KPIs come from the run summary (the dashboard's own
computation), every other section from the run's stored insights. A module that was skipped or
failed becomes `Unavailable` with its reason, never an empty section. Customer-level data is never
read: segments and churn are aggregates, and contributor tables keep only product, category and
region dimensions whose column is not flagged as personal data.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.repository import ColumnProfileRepository, InsightRepository
from app.ml.roles import RoleMap
from app.models.analysis import AnalysisRun, Insight
from app.models.dataset import Dataset, DatasetVersion

REPORT_DIMENSIONS = ("product", "category", "region")
SUMMARY_FINDINGS = 5
TOP_ITEMS = 10
UNUSUAL_DAYS = 10
DEAD_STOCK_NAMES = 10
QUALITY_ISSUES = 5
APPENDIX_INSIGHTS = 200
FAILED_REASON = "The module failed during the analysis; running the analysis again may help."
EMPTY_REASON = {
    "kpis": "The analysis produced no headline figures for this file.",
    "forecast": "No forecast was produced for this file.",
    "pareto": "No product, category or region figures were produced for this file.",
    "anomalies": "No unusual days were found.",
    "segmentation": "No customer segments were produced for this file.",
    "churn": "No churn risk figures were produced for this file.",
}

Confidence = Literal["high", "medium", "low"]


class Unavailable(BaseModel):
    """A section that has nothing to show, and why."""

    kind: Literal["skipped", "failed", "empty"]
    reason: str


class Finding(BaseModel):
    """One executive-summary finding."""

    title: str
    summary: str
    module: str
    type: str
    impact_score: float
    confidence: Confidence
    sample_size: int


class KpiRow(BaseModel):
    """One headline figure with the change vs the previous window (dashboard definitions)."""

    key: str
    unit: str
    value: float | None
    previous_value: float | None
    change: float | None
    change_unit: str
    reason: str | None


class KpiSection(BaseModel):
    """The dashboard's default KPI cards."""

    range: dict[str, str]
    previous_range: dict[str, str]
    rows: list[KpiRow]
    confidence: Confidence
    sample_size: int
    warnings: list[str]


class TrendPoint(BaseModel):
    """One history point."""

    period: str
    value: float


class ForecastPoint(BaseModel):
    """One forecast point with its 80% and 95% ranges."""

    period: str
    point: float
    lo80: float
    hi80: float
    lo95: float
    hi95: float


class TrendSection(BaseModel):
    """History and forecast of the total."""

    frequency: str
    metric: str
    model: str | None
    history: list[TrendPoint]
    forecast: list[ForecastPoint]
    confidence: Confidence
    sample_size: int
    no_better_than_baseline: bool


class ContributorItem(BaseModel):
    """One item of a Pareto table."""

    item: str
    revenue: float
    share_pct: float
    cumulative_pct: float
    abc: str


class ContributorTable(BaseModel):
    """Top items of one dimension with the ABC class."""

    dimension: str
    total_revenue: float
    item_count: int
    abc_counts: dict[str, int]
    items: list[ContributorItem]
    confidence: Confidence
    sample_size: int


class DeadStock(BaseModel):
    """Products without recent sales."""

    count: int
    product_count: int | None
    days: int | None
    as_of: str | None
    names: list[str]


class UnusualDay(BaseModel):
    """One unusual period of net revenue."""

    start: str
    end: str
    kind: str
    actual: float
    expected: float
    change: float | None
    contributors: list[str]


class UnusualSection(BaseModel):
    """Unusual days found by the anomaly module."""

    unit: str | None
    total: int
    days: list[UnusualDay]
    confidence: Confidence
    sample_size: int


class Segment(BaseModel):
    """One RFM segment (aggregate only)."""

    segment: str
    customers: int
    revenue_share_pct: float
    revenue: float
    action: str | None


class SegmentSection(BaseModel):
    """Customer segments and recommended actions."""

    customers: int
    segments: list[Segment]
    confidence: Confidence
    sample_size: int


class ChurnBand(BaseModel):
    """Customers in one risk band (aggregate only)."""

    customers: int
    yearly_spend: float | None
    expected_loss: float | None


class ChurnSection(BaseModel):
    """Churn risk as band totals; no customer is named."""

    basis: str
    scored_customers: int
    window_days: int | None
    high: ChurnBand
    medium: int
    low: int
    top_10pct: ChurnBand
    confidence: Confidence
    sample_size: int


class EvidenceItem(BaseModel):
    """One query behind a finding: what it shows, the exact SQL and how many rows it returned."""

    finding: str
    purpose: str
    sql: str
    row_count: int


class ModuleState(BaseModel):
    """How each analysis module ended."""

    module: str
    status: str
    reason: str | None


class Meta(BaseModel):
    """Cover and page header data."""

    report_id: str
    dataset_name: str
    version: int
    locale: Literal["en", "ar"]
    run_id: str
    run_date: str
    generated_at: str
    period_start: str | None
    period_end: str | None
    rows: int | None
    quality_score: int | None
    currency: str | None


class ReportFacts(BaseModel):
    """Everything a report prints. Saved as JSON next to the PDF."""

    meta: Meta
    findings: list[Finding]
    kpis: KpiSection | Unavailable
    trend: TrendSection | Unavailable
    contributors: list[ContributorTable] | Unavailable
    dead_stock: DeadStock | None
    unusual: UnusualSection | Unavailable
    segments: SegmentSection | Unavailable
    churn: ChurnSection | Unavailable
    caveats: list[str]
    quality_issues: list[str]
    modules: list[ModuleState]
    appendix: list[EvidenceItem]


# ---- helpers --------------------------------------------------------------------------------


def _state(run: AnalysisRun, module: str) -> dict[str, Any]:
    found: dict[str, Any] = (run.progress or {}).get("modules", {}).get(module, {})
    return found


def _unavailable(run: AnalysisRun, module: str) -> Unavailable | None:
    """Return why a module has nothing to show, or None when it completed."""
    state = _state(run, module)
    status = state.get("status")
    if status == "skipped":
        return Unavailable(kind="skipped", reason=str(state.get("reason") or "Skipped."))
    if status == "failed":
        return Unavailable(kind="failed", reason=FAILED_REASON)
    if status != "completed":
        return Unavailable(kind="skipped", reason="The module did not run in this analysis.")
    return None


def _empty(module: str) -> Unavailable:
    return Unavailable(kind="empty", reason=EMPTY_REASON[module])


def _first(insights: list[Insight], kind: str) -> Insight | None:
    return next((i for i in insights if i.type == kind), None)


def _f(value: Any) -> float:
    return float(value or 0)


def _confidence(insight: Insight) -> Confidence:
    level = insight.confidence
    return level if level in ("high", "medium", "low") else "low"  # type: ignore[return-value]


def pii_roles(
    db: Session, tenant_id: uuid.UUID, version: DatasetVersion, run: AnalysisRun
) -> set[str]:
    """Return the roles whose column is flagged as personal data (always includes customer_id)."""
    profiles = ColumnProfileRepository(db, tenant_id).for_version(version.id)
    order = [m["normalized"] for m in (version.column_map or [])]
    roles = RoleMap.from_profiles(profiles, order, (run.config or {}).get("date_column"))
    flagged = {p.name for p in profiles if p.pii_flag}
    out = {"customer_id"}
    for role, column in roles.columns.items():
        if column in flagged:
            out.add(role)
    return out


# ---- sections -------------------------------------------------------------------------------


def _findings(insights: list[Insight]) -> list[Finding]:
    return [
        Finding(
            title=i.title,
            summary=i.summary,
            module=i.module,
            type=i.type,
            impact_score=round(i.impact_score, 1),
            confidence=_confidence(i),
            sample_size=i.sample_size,
        )
        for i in insights[:SUMMARY_FINDINGS]
    ]


def _kpis(run: AnalysisRun) -> KpiSection | Unavailable:
    summary = run.summary or {}
    block = summary.get("kpis")
    if not block:
        reason = summary.get("kpis_reason")
        return Unavailable(kind="skipped", reason=reason) if reason else _empty("kpis")
    return KpiSection(
        range=block["range"],
        previous_range=block["previous_range"],
        rows=[KpiRow.model_validate(k) for k in block["kpis"]],
        confidence=block["confidence"],
        sample_size=int(block["sample_size"]),
        warnings=[str(w) for w in block.get("warnings", [])],
    )


def _trend(run: AnalysisRun, insights: list[Insight]) -> TrendSection | Unavailable:
    missing = _unavailable(run, "forecast")
    if missing:
        return missing
    found = _first(insights, "forecast_total")
    if found is None:
        return _empty("forecast")
    chart, p = found.chart_spec or {}, found.payload or {}
    return TrendSection(
        frequency=str(p.get("frequency") or chart.get("unit") or ""),
        metric=str(p.get("metric") or "revenue"),
        model=p.get("model"),
        history=[
            TrendPoint(period=h["period"], value=_f(h["value"])) for h in chart.get("history", [])
        ],
        forecast=[ForecastPoint.model_validate(f) for f in chart.get("forecast", [])],
        confidence=_confidence(found),
        sample_size=found.sample_size,
        no_better_than_baseline=bool(p.get("no_better_than_baseline")),
    )


def _contributors(
    run: AnalysisRun, insights: list[Insight], hidden: set[str]
) -> list[ContributorTable] | Unavailable:
    missing = _unavailable(run, "pareto")
    if missing:
        return missing
    tables = []
    for dimension in REPORT_DIMENSIONS:
        if dimension in hidden:
            continue
        found = next(
            (i for i in insights if i.type == "pareto" and i.payload.get("dimension") == dimension),
            None,
        )
        if found is None:
            continue
        p = found.payload
        tables.append(
            ContributorTable(
                dimension=dimension,
                total_revenue=_f(p.get("total_revenue")),
                item_count=int(p.get("item_count") or 0),
                abc_counts={k: int(v) for k, v in (p.get("abc_counts") or {}).items()},
                items=[
                    ContributorItem(
                        item=str(x["item"]),
                        revenue=_f(x["revenue"]),
                        share_pct=_f(x["share_pct"]),
                        cumulative_pct=_f(x["cumulative_pct"]),
                        abc=str(x["abc"]),
                    )
                    for x in (p.get("items") or [])[:TOP_ITEMS]
                ],
                confidence=_confidence(found),
                sample_size=found.sample_size,
            )
        )
    return tables or _empty("pareto")


def _dead_stock(insights: list[Insight], hidden: set[str]) -> DeadStock | None:
    found = _first(insights, "dead_stock")
    if found is None or "product" in hidden:
        return None
    p = found.payload
    names = [str(n) for n in (p.get("item_names") or [])]
    return DeadStock(
        count=int(p.get("dead_count") or len(names)),
        product_count=p.get("product_count"),
        days=p.get("days"),
        as_of=str(p.get("as_of"))[:10] if p.get("as_of") else None,
        names=names[:DEAD_STOCK_NAMES],
    )


def _contributor_names(insight: Insight | None, hidden: set[str]) -> list[str]:
    if insight is None:
        return []
    out: list[str] = []
    for role, items in (insight.payload.get("likely_contributors") or {}).items():
        if role not in REPORT_DIMENSIONS or role in hidden:
            continue
        out.extend(str(x["item"]) for x in items[:2])
    return out[:3]


def _unusual(
    run: AnalysisRun, insights: list[Insight], hidden: set[str]
) -> UnusualSection | Unavailable:
    missing = _unavailable(run, "anomalies")
    if missing:
        return missing
    summary = _first(insights, "anomaly_summary")
    if summary is None:
        return _empty("anomalies")
    detail = {i.payload.get("start"): i for i in insights if i.type == "anomaly"}
    events = summary.payload.get("events") or []
    ranked = sorted(events, key=lambda e: abs(_f(e.get("change"))), reverse=True)[:UNUSUAL_DAYS]
    ranked.sort(key=lambda e: str(e.get("start")))
    days = [
        UnusualDay(
            start=str(e["start"])[:10],
            end=str(e.get("end") or e["start"])[:10],
            kind=str(e.get("kind")),
            actual=_f(e.get("actual")),
            expected=_f(e.get("expected")),
            change=e.get("change"),
            contributors=_contributor_names(detail.get(e["start"]), hidden),
        )
        for e in ranked
    ]
    return UnusualSection(
        unit=summary.payload.get("unit"),
        total=len(events),
        days=days,
        confidence=_confidence(summary),
        sample_size=summary.sample_size,
    )


def _segments(run: AnalysisRun, insights: list[Insight]) -> SegmentSection | Unavailable:
    missing = _unavailable(run, "segmentation")
    if missing:
        return missing
    found = _first(insights, "rfm_segments")
    if found is None:
        return _empty("segmentation")
    rows = found.payload.get("segments") or []
    segments = [
        Segment(
            segment=str(s["segment"]),
            customers=int(s["customers"]),
            revenue_share_pct=_f(s.get("revenue_share_pct")),
            revenue=_f(s.get("revenue")),
            action=(s.get("recommended_action") or {}).get("text"),
        )
        for s in rows
    ]
    return SegmentSection(
        customers=sum(s.customers for s in segments),
        segments=segments,
        confidence=_confidence(found),
        sample_size=found.sample_size,
    )


def _band(raw: dict[str, Any] | None) -> ChurnBand:
    raw = raw or {}
    return ChurnBand(
        customers=int(raw.get("customers") or 0),
        yearly_spend=raw.get("yearly_spend"),
        expected_loss=raw.get("expected_loss"),
    )


def _churn(run: AnalysisRun, insights: list[Insight]) -> ChurnSection | Unavailable:
    missing = _unavailable(run, "churn")
    if missing:
        return missing
    found = _first(insights, "churn_risk")
    if found is None:
        return _empty("churn")
    p = found.payload
    bands = p.get("bands") or {}
    return ChurnSection(
        basis=str(p.get("basis") or "probability"),
        scored_customers=int(p.get("scored_customers") or 0),
        window_days=p.get("window_days"),
        high=_band(bands.get("high")),
        medium=int((bands.get("medium") or {}).get("customers") or 0),
        low=int((bands.get("low") or {}).get("customers") or 0),
        top_10pct=_band(bands.get("top_10pct")),
        confidence=_confidence(found),
        sample_size=found.sample_size,
    )


def _caveats(run: AnalysisRun, kpis: KpiSection | Unavailable) -> list[str]:
    seen: list[str] = []
    for state in ((run.progress or {}).get("modules") or {}).values():
        for warning in state.get("warnings") or []:
            if str(warning) not in seen:
                seen.append(str(warning))
    if isinstance(kpis, KpiSection):
        seen.extend(w for w in kpis.warnings if w not in seen)
    return seen


def _quality_issues(version: DatasetVersion) -> list[str]:
    issues = (version.quality or {}).get("issues") or []
    out = []
    for issue in issues[:QUALITY_ISSUES]:
        title, detail = str(issue.get("title") or ""), str(issue.get("detail") or "")
        out.append(f"{title}: {detail}" if detail else title)
    return out


def _modules(run: AnalysisRun) -> list[ModuleState]:
    out = []
    for name, state in ((run.progress or {}).get("modules") or {}).items():
        status = str(state.get("status"))
        reason = state.get("reason") if status == "skipped" else None
        if status == "failed":
            reason = FAILED_REASON
        out.append(ModuleState(module=name, status=status, reason=reason))
    return out


def _appendix(run: AnalysisRun, insights: list[Insight]) -> list[EvidenceItem]:
    out = []
    block = (run.summary or {}).get("kpis") or {}
    for q in block.get("evidence") or []:
        out.append(
            EvidenceItem(
                finding="Headline figures (KPI table)",
                purpose=str(q.get("purpose") or ""),
                sql=str(q.get("sql") or ""),
                row_count=int(q.get("row_count") or 0),
            )
        )
    for insight in insights:
        for q in (insight.evidence or {}).get("queries") or []:
            out.append(
                EvidenceItem(
                    finding=insight.title,
                    purpose=str(q.get("purpose") or ""),
                    sql=str(q.get("sql") or ""),
                    row_count=int(q.get("row_count") or 0),
                )
            )
    return out


def build_report_facts(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    report_id: uuid.UUID,
    dataset: Dataset,
    version: DatasetVersion,
    run: AnalysisRun,
    locale: Literal["en", "ar"],
    now: datetime,
) -> ReportFacts:
    """Return the facts of one report, read from the run's stored results."""
    insights = InsightRepository(db, tenant_id).for_run(run.id, limit=APPENDIX_INSIGHTS)
    hidden = pii_roles(db, tenant_id, version, run)
    kpis = _kpis(run)
    summary = run.summary or {}
    block = summary.get("kpis") or {}
    data_range = block.get("data_range") or (version.dataset_profile or {}).get("date_range") or {}
    start = data_range.get("start") or data_range.get("min")
    end = data_range.get("end") or data_range.get("max")
    finished = run.finished_at or run.created_at
    return ReportFacts(
        meta=Meta(
            report_id=str(report_id),
            dataset_name=dataset.name,
            version=version.version,
            locale=locale,
            run_id=str(run.id),
            run_date=finished.date().isoformat(),
            generated_at=now.replace(microsecond=0).isoformat(),
            period_start=str(start)[:10] if start else None,
            period_end=str(end)[:10] if end else None,
            rows=version.row_count,
            quality_score=version.quality_score,
            currency=block.get("currency") or (run.config or {}).get("currency"),
        ),
        findings=_findings(insights),
        kpis=kpis,
        trend=_trend(run, insights),
        contributors=_contributors(run, insights, hidden),
        dead_stock=_dead_stock(insights, hidden),
        unusual=_unusual(run, insights, hidden),
        segments=_segments(run, insights),
        churn=_churn(run, insights),
        caveats=_caveats(run, kpis),
        quality_issues=_quality_issues(version),
        modules=_modules(run),
        appendix=_appendix(run, insights),
    )
