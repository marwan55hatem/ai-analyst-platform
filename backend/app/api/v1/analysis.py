"""Analysis endpoints: capabilities, (re-)run, run status and insights."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from app.api import responses
from app.api.deps import CurrentUser, DatasetRepo, DbSession
from app.db.repository import AnalysisRunRepository, InsightRepository
from app.schemas.analysis import (
    AnalysisRunOut,
    AnalyzeRequest,
    CapabilitiesOut,
    Confidence,
    InsightOut,
    InsightSummary,
)
from app.services import analysis
from app.services.profiling import service as profiling

router = APIRouter(
    tags=["analysis"],
    responses={**responses.UNAUTHORIZED, **responses.NOT_FOUND},
)

VersionNumber = Annotated[int | None, Query(ge=1, description="Dataset version (default current)")]


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
    confidence: Confidence | None = None,
    module: Annotated[str | None, Query(max_length=50)] = None,
    sort: Literal["impact_score"] = "impact_score",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[InsightSummary]:
    """Return a run's insights, filtered by type, confidence or module."""
    AnalysisRunRepository(db, user.tenant_id).get_or_404(run_id)
    found = InsightRepository(db, user.tenant_id).for_run(
        run_id, type=type, confidence=confidence, module=module, limit=limit, offset=offset
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
