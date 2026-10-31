"""Analysis endpoints: capabilities, (re-)run, run status and insights."""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response, status

from app.api import responses
from app.api.deps import CurrentUser, DatasetRepo, DbSession
from app.db.repository import AnalysisRunRepository, InsightRepository
from app.schemas.analysis import (
    AnalysisRunListItem,
    AnalysisRunOut,
    AnalyzeRequest,
    AnomaliesOut,
    CapabilitiesOut,
    ChurnOut,
    Confidence,
    DriversOut,
    InsightOut,
    InsightSummary,
    SegmentsOut,
)
from app.services import analysis, exports, findings
from app.services.profiling import service as profiling

router = APIRouter(
    tags=["analysis"],
    responses={**responses.UNAUTHORIZED, **responses.NOT_FOUND},
)

VersionNumber = Annotated[int | None, Query(ge=1, description="Dataset version (default current)")]
SegmentSort = Literal["customer_key", "monetary", "frequency", "recency_days"]
ChurnSort = Literal["probability", "revenue_at_stake", "expected_loss", "recency_days", "monetary"]
Order = Literal["asc", "desc"]
RiskBand = Literal["high", "medium", "low"]
RankBand = Literal["top_10pct", "rest"]
CSV: dict[int | str, dict[str, Any]] = {
    200: {"content": {"text/csv": {}}, "description": "CSV file"}
}


def _csv(body: str, filename: str) -> Response:
    """Return CSV text as a download."""
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/datasets/{dataset_id}/capabilities",
    response_model=CapabilitiesOut,
    summary="Which analyses this dataset supports, and why others are skipped",
    responses={**responses.CONFLICT, **responses.UNPROCESSABLE},
)
def get_capabilities(
    dataset_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    repo: DatasetRepo,
    version: VersionNumber = None,
    date_column: Annotated[str | None, Query(max_length=200)] = None,
) -> CapabilitiesOut:
    """Return the capability of every analysis module for a version."""
    target = profiling.resolve_version(db, repo.get_or_404(dataset_id), version)
    payload = analysis.capabilities_payload(db, user.tenant_id, target, date_column)
    return CapabilitiesOut.model_validate(payload)


@router.post(
    "/datasets/{dataset_id}/analyze",
    response_model=AnalysisRunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run (or re-run) the analysis; a re-run replaces the version's insights",
    responses={**responses.CONFLICT, **responses.UNPROCESSABLE},
)
def analyze(
    dataset_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    repo: DatasetRepo,
    body: AnalyzeRequest | None = None,
    version: VersionNumber = None,
) -> AnalysisRunOut:
    """Queue an analysis run for a version and return it."""
    target = profiling.resolve_version(db, repo.get_or_404(dataset_id), version)
    explicit = {} if body is None else body.model_dump(exclude_none=True)
    run = analysis.start_analysis(db, user.tenant_id, target, explicit)
    db.refresh(run)
    return AnalysisRunOut.model_validate(run)


@router.get(
    "/datasets/{dataset_id}/analysis-runs",
    response_model=list[AnalysisRunListItem],
    summary="Analysis runs of a dataset, newest first (finds the automatic run)",
)
def list_runs(
    dataset_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    repo: DatasetRepo,
    version: VersionNumber = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[AnalysisRunListItem]:
    """Return the dataset's runs with their version number and insight count."""
    repo.get_or_404(dataset_id)
    found = AnalysisRunRepository(db, user.tenant_id).list_for_dataset(
        dataset_id, version=version, limit=limit
    )
    return [
        AnalysisRunListItem(
            id=run.id,
            dataset_version_id=run.dataset_version_id,
            version=number,
            status=run.status,
            progress=run.progress,
            started_at=run.started_at,
            finished_at=run.finished_at,
            error=run.error,
            insight_count=count,
        )
        for run, number, count in found
    ]


@router.get(
    "/analysis-runs/{run_id}",
    response_model=AnalysisRunOut,
    summary="Status and per-module progress of a run",
)
def get_run(run_id: uuid.UUID, user: CurrentUser, db: DbSession) -> AnalysisRunOut:
    """Return one analysis run of the caller's tenant."""
    run = AnalysisRunRepository(db, user.tenant_id).get_or_404(run_id)
    return AnalysisRunOut.model_validate(run)


@router.get(
    "/analysis-runs/{run_id}/insights",
    response_model=list[InsightSummary],
    summary="Insights of a run, highest impact first",
)
def list_insights(
    run_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    type: Annotated[str | None, Query(max_length=50)] = None,
    types: Annotated[list[str] | None, Query(max_length=20)] = None,
    confidence: Confidence | None = None,
    module: Annotated[str | None, Query(max_length=50)] = None,
    sort: Literal["impact_score"] = "impact_score",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[InsightSummary]:
    """Return a run's insights, filtered by one type or several, confidence or module."""
    AnalysisRunRepository(db, user.tenant_id).get_or_404(run_id)
    found = InsightRepository(db, user.tenant_id).for_run(
        run_id,
        type=type,
        types=types,
        confidence=confidence,
        module=module,
        limit=limit,
        offset=offset,
    )
    return [InsightSummary.model_validate(i) for i in found]


@router.get(
    "/insights/{insight_id}",
    response_model=InsightOut,
    summary="One insight with its numbers, chart spec and SQL evidence",
)
def get_insight(insight_id: uuid.UUID, user: CurrentUser, db: DbSession) -> InsightOut:
    """Return one insight of the caller's tenant."""
    return InsightOut.model_validate(InsightRepository(db, user.tenant_id).get_or_404(insight_id))


@router.get(
    "/analysis-runs/{run_id}/anomalies",
    response_model=AnomaliesOut,
    summary="Unusual periods, level shifts and odd rows found by a run",
)
def get_anomalies(run_id: uuid.UUID, user: CurrentUser, db: DbSession) -> AnomaliesOut:
    """Return the anomalies of one run of the caller's tenant, with likely contributors."""
    return AnomaliesOut.model_validate(findings.anomalies_payload(db, user.tenant_id, run_id))


SegmentName = Literal[
    "Champions", "Loyal", "Potential loyalists", "New", "At risk", "Hibernating", "Lost"
]


@router.get(
    "/analysis-runs/{run_id}/segments",
    response_model=SegmentsOut,
    summary="Customer segments of a run and a page of per-customer assignments",
)
def get_segments(
    run_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    segment: SegmentName | None = None,
    cluster: Annotated[int | None, Query(ge=0, le=20)] = None,
    sort: SegmentSort = "customer_key",
    order: Order = "asc",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SegmentsOut:
    """Return RFM segments, clusters and assignments of one run of the caller's tenant."""
    found = findings.segments_payload(
        db,
        user.tenant_id,
        run_id,
        segment=segment,
        cluster=cluster,
        sort=sort,
        descending=order == "desc",
        limit=limit,
        offset=offset,
    )
    return SegmentsOut.model_validate(found)


@router.get(
    "/analysis-runs/{run_id}/segments.csv",
    response_class=Response,
    responses=CSV,
    summary="Customer segment assignments of a run as CSV (same filters and sort as the page)",
)
def export_segments(
    run_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    segment: SegmentName | None = None,
    cluster: Annotated[int | None, Query(ge=0, le=20)] = None,
    sort: SegmentSort = "customer_key",
    order: Order = "asc",
) -> Response:
    """Return the run's assignments as a CSV download (pseudonyms kept, formulas neutralised)."""
    body = exports.segments_csv(
        db,
        user.tenant_id,
        run_id,
        segment=segment,
        cluster=cluster,
        sort=sort,
        descending=order == "desc",
    )
    return _csv(body, f"segments-{run_id}.csv")


@router.get(
    "/analysis-runs/{run_id}/churn",
    response_model=ChurnOut,
    summary="Estimated churn risk of a run: model check, bands and a page of customers",
)
def get_churn(
    run_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    risk_band: RiskBand | None = None,
    rank_band: RankBand | None = None,
    sort: ChurnSort = "probability",
    order: Order = "desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ChurnOut:
    """Return the churn model summary and customers (highest estimated risk first by default)."""
    found = findings.churn_payload(
        db,
        user.tenant_id,
        run_id,
        risk_band=risk_band,
        rank_band=rank_band,
        sort=sort,
        descending=order == "desc",
        limit=limit,
        offset=offset,
    )
    return ChurnOut.model_validate(found)


@router.get(
    "/analysis-runs/{run_id}/churn.csv",
    response_class=Response,
    responses=CSV,
    summary="Per-customer churn risk of a run with the top 3 reasons as CSV",
)
def export_churn(
    run_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    risk_band: RiskBand | None = None,
    rank_band: RankBand | None = None,
    sort: ChurnSort = "probability",
    order: Order = "desc",
) -> Response:
    """Return the run's churn scores as a CSV download (pseudonyms kept, formulas neutralised)."""
    body = exports.churn_csv(
        db,
        user.tenant_id,
        run_id,
        risk_band=risk_band,
        rank_band=rank_band,
        sort=sort,
        descending=order == "desc",
    )
    return _csv(body, f"churn-{run_id}.csv")


@router.get(
    "/analysis-runs/{run_id}/drivers",
    response_model=DriversOut,
    summary="Factors associated with revenue per line (association, not proven cause)",
)
def get_drivers(run_id: uuid.UUID, user: CurrentUser, db: DbSession) -> DriversOut:
    """Return the driver importances and partial dependence of one run."""
    return DriversOut.model_validate(findings.drivers_payload(db, user.tenant_id, run_id))
