"""Dashboard time series service: open the version's analysis table and aggregate one range.

Like what-if it runs inside the request (two aggregations) and shares the what-if slots, so at
most WHATIF_MAX_CONCURRENT DuckDB tables are open per API process.
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import UnprocessableError
from app.ml.timeseries import Grain, SeriesUnavailable, compute
from app.models.dataset import DatasetVersion
from app.services.analysis import analysis_context, merged_config
from app.services.profiling.service import require_profile
from app.services.whatif import table_slot


def time_series(
    db: Session,
    tenant_id: uuid.UUID,
    version: DatasetVersion,
    *,
    start: date | None,
    end: date | None,
    grain: Grain,
) -> dict[str, Any]:
    """Return KPIs, the previous-window comparison and a series for one version of the tenant."""
    require_profile(version)
    config = merged_config(db, tenant_id, version, {})
    try:
        with table_slot("timeseries_busy"), analysis_context(db, tenant_id, version, config) as ctx:
            found = compute(ctx, start, end, grain, get_settings().timeseries_max_points)
    except SeriesUnavailable as exc:
        raise UnprocessableError(exc.message, code=exc.code) from exc
    return {
        "dataset_id": version.dataset_id,
        "dataset_version_id": version.id,
        "version": version.version,
        **found,
    }
