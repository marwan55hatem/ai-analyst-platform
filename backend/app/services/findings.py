"""Read models for the anomaly and segment endpoints (assembled from stored insights)."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.repository import (
    AnalysisRunRepository,
    InsightRepository,
    SegmentAssignmentRepository,
)
from app.models.analysis import AnalysisRun, Insight

ANOMALY_MODULE = "anomalies"
SEGMENT_MODULE = "segmentation"


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
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Return a run's RFM segments, cluster profiles and one page of customer assignments."""
    run = AnalysisRunRepository(db, tenant_id).get_or_404(run_id)
    state = module_state(run, SEGMENT_MODULE)
    insights = _module_insights(db, tenant_id, run.id, SEGMENT_MODULE)
    rfm = next((i.payload for i in insights if i.type == "rfm_segments"), {})
    groups = next((i.payload for i in insights if i.type == "customer_clusters"), {})
    items, total = SegmentAssignmentRepository(db, tenant_id).page(
        run.id, segment=segment, cluster=cluster, limit=limit, offset=offset
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
        "assignments": {"items": items, "total": total, "limit": limit, "offset": offset},
        "insights": insights,
    }
