"""Read models for the anomaly, segment, churn and driver endpoints (from stored insights)."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.repository import (
    AnalysisRunRepository,
    ChurnScoreRepository,
    InsightRepository,
    SegmentAssignmentRepository,
)
from app.models.analysis import AnalysisRun, Insight

ANOMALY_MODULE = "anomalies"
SEGMENT_MODULE = "segmentation"
CHURN_MODULE = "churn"
DRIVER_MODULE = "drivers"


def module_state(run: AnalysisRun, module: str) -> dict[str, Any]:
    """Return the module's progress entry of a run (empty while it has not run yet)."""
    modules = (run.progress or {}).get("modules", {})
    state: dict[str, Any] = modules.get(module, {})
    return state


def _module_insights(
    db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID, module: str
) -> list[Insight]:
    return InsightRepository(db, tenant_id).for_run(run_id, module=module, limit=500)


def anomalies_payload(db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any]:
    """Return a run's anomalies: events with likely contributors, shifts, rows and insights."""
    run = AnalysisRunRepository(db, tenant_id).get_or_404(run_id)
    state = module_state(run, ANOMALY_MODULE)
    insights = _module_insights(db, tenant_id, run.id, ANOMALY_MODULE)
    summary = next((i for i in insights if i.type == "anomaly_summary"), None)
    detail = {i.payload.get("start"): i for i in insights if i.type == "anomaly"}
    events: list[dict[str, Any]] = []
    for event in summary.payload.get("events", []) if summary else []:
        insight = detail.get(event["start"])
        events.append(
            {
                **event,
                "likely_contributors": (
                    insight.payload.get("likely_contributors", {}) if insight else {}
                ),
                "insight_id": insight.id if insight else None,
            }
        )
    rows = next((i for i in insights if i.type == "row_anomalies"), None)
    return {
        "run_id": run.id,
        "status": run.status,
        "module_status": state.get("status"),
        "skipped_reason": state.get("reason"),
        "warnings": state.get("warnings", []),
        "unit": summary.payload.get("unit") if summary else None,
        "events": events,
        "level_shifts": summary.payload.get("level_shifts", []) if summary else [],
        "row_anomalies": (
            {
                k: rows.payload.get(k)
                for k in (
                    "counts",
                    "rules",
                    "samples",
                    "dataset_version_id",
                    "dataset_version_number",
                    "row_numbers_refer_to",
                )
            }
            if rows
            else None
        ),
        "insights": insights,
    }


def segments_payload(
    db: Session,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    segment: str | None,
    cluster: int | None,
    sort: str = "customer_key",
    descending: bool = False,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Return a run's RFM segments, cluster profiles and one page of customer assignments."""
    run = AnalysisRunRepository(db, tenant_id).get_or_404(run_id)
    state = module_state(run, SEGMENT_MODULE)
    insights = _module_insights(db, tenant_id, run.id, SEGMENT_MODULE)
    rfm = next((i.payload for i in insights if i.type == "rfm_segments"), {})
    groups = next((i.payload for i in insights if i.type == "customer_clusters"), {})
    assignments = SegmentAssignmentRepository(db, tenant_id)
    items, total = assignments.page(
        run.id,
        segment=segment,
        cluster=cluster,
        sort=sort,
        descending=descending,
        limit=limit,
        offset=offset,
    )
    return {
        "run_id": run.id,
        "status": run.status,
        "module_status": state.get("status"),
        "skipped_reason": state.get("reason"),
        "warnings": state.get("warnings", []),
        "pseudonymized_customer_ids": bool(rfm.get("pseudonymized_customer_ids", False)),
        "segments": rfm.get("segments", []),
        "clusters": groups.get("clusters", []),
        "k": groups.get("k"),
        "stability": groups.get("stability"),
        "rfm_grid": assignments.grid(run.id),
        "assignments": {"items": items, "total": total, "limit": limit, "offset": offset},
        "insights": insights,
    }


def churn_payload(
    db: Session,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    risk_band: str | None,
    rank_band: str | None,
    sort: str = "probability",
    descending: bool = True,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Return a run's churn model summary, risk bands and one page of per-customer risk."""
    run = AnalysisRunRepository(db, tenant_id).get_or_404(run_id)
    state = module_state(run, CHURN_MODULE)
    insights = _module_insights(db, tenant_id, run.id, CHURN_MODULE)
    risk = next((i.payload for i in insights if i.type == "churn_risk"), {})
    model = next((i.payload for i in insights if i.type == "churn_model"), {})
    items, total = ChurnScoreRepository(db, tenant_id).page(
        run.id,
        risk_band=risk_band,
        rank_band=rank_band,
        sort=sort,
        descending=descending,
        limit=limit,
        offset=offset,
    )
    return {
        "run_id": run.id,
        "status": run.status,
        "module_status": state.get("status"),
        "skipped_reason": state.get("reason"),
        "warnings": state.get("warnings", []),
        "pseudonymized_customer_ids": bool(risk.get("pseudonymized_customer_ids", False)),
        "basis": risk.get("basis"),
        "bands": risk.get("bands", {}),
        "importance": risk.get("importance", []),
        "model": {
            k: model.get(k) for k in ("models", "winner", "validation", "calibration", "label")
        }
        if model
        else None,
        "customers": {"items": items, "total": total, "limit": limit, "offset": offset},
        "insights": insights,
    }


def drivers_payload(db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any]:
    """Return a run's revenue drivers: importances by both methods and partial dependence."""
    run = AnalysisRunRepository(db, tenant_id).get_or_404(run_id)
    state = module_state(run, DRIVER_MODULE)
    insights = _module_insights(db, tenant_id, run.id, DRIVER_MODULE)
    payload = next((i.payload for i in insights if i.type == "revenue_drivers"), {})
    return {
        "run_id": run.id,
        "status": run.status,
        "module_status": state.get("status"),
        "skipped_reason": state.get("reason"),
        "warnings": state.get("warnings", []),
        "importance": payload.get("importance", []),
        "partial_dependence": payload.get("partial_dependence", {}),
        "r2_holdout": payload.get("r2_holdout"),
        "methods_agree_on_top": payload.get("methods_agree_on_top"),
        "association_not_causation": True,
        "insights": insights,
    }
