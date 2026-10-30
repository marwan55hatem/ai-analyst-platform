"""CSV exports of a run's per-customer tables (segments and churn), with the page filters and sort.

Customer keys are written exactly as stored, so personal ids stay pseudonymised. Text cells that
a spreadsheet would read as a formula are prefixed with an apostrophe (CSV injection).
"""

import csv
import io
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.repository import (
    AnalysisRunRepository,
    ChurnScoreRepository,
    SegmentAssignmentRepository,
)

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
SEGMENT_COLUMNS = (
    "customer_key",
    "rfm_segment",
    "r_score",
    "f_score",
    "m_score",
    "recency_days",
    "frequency",
    "monetary",
    "cluster",
    "cluster_name",
)
CHURN_COLUMNS = (
    "customer_key",
    "probability",
    "risk_band",
    "rank_band",
    "revenue_at_stake",
    "expected_loss",
    "recency_days",
    "frequency",
    "monetary",
)
REASON_COLUMNS = ("reason_1", "reason_2", "reason_3")


def safe_cell(value: Any) -> Any:
    """Return the value, with text that starts like a formula neutralised by a leading quote."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def _to_csv(header: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(v) for v in row])
    return out.getvalue()


def segments_csv(
    db: Session,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    segment: str | None,
    cluster: int | None,
    sort: str,
    descending: bool,
) -> str:
    """Return a run's customer assignments as CSV (at most EXPORT_MAX_ROWS rows)."""
    run = AnalysisRunRepository(db, tenant_id).get_or_404(run_id)
    found = SegmentAssignmentRepository(db, tenant_id).stream(
        run.id,
        segment=segment,
        cluster=cluster,
        sort=sort,
        descending=descending,
        cap=get_settings().export_max_rows,
    )
    rows = ([getattr(a, c) for c in SEGMENT_COLUMNS] for a in found)
    return _to_csv(SEGMENT_COLUMNS, rows)


def churn_csv(
    db: Session,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    risk_band: str | None,
    rank_band: str | None,
    sort: str,
    descending: bool,
) -> str:
    """Return a run's per-customer churn risk with its top 3 reasons as CSV."""
    run = AnalysisRunRepository(db, tenant_id).get_or_404(run_id)
    found = ChurnScoreRepository(db, tenant_id).stream(
        run.id,
        risk_band=risk_band,
        rank_band=rank_band,
        sort=sort,
        descending=descending,
        cap=get_settings().export_max_rows,
    )

    def row(score: Any) -> list[Any]:
        reasons = [r.get("text", "") for r in (score.reasons or [])][:3]
        return [getattr(score, c) for c in CHURN_COLUMNS] + reasons + [""] * (3 - len(reasons))

    return _to_csv(CHURN_COLUMNS + REASON_COLUMNS, (row(s) for s in found))
