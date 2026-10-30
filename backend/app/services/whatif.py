"""What-if service: open the version's analysis table and answer up to 3 scenarios.

It runs inside the request because the work is one aggregation and a small regression per scope;
the same hardened, memory-limited DuckDB table as an analysis run is used, and at most
WHATIF_MAX_CONCURRENT requests per API process hold one at a time (others wait, then get 409).
"""

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError
from app.ml.whatif.scenarios import Scenario, run
from app.models.dataset import DatasetVersion
from app.schemas.whatif import WhatIfRequest
from app.services.analysis import analysis_context, merged_config
from app.services.profiling.service import require_profile

WAIT_SECONDS = 20.0
_slots: threading.BoundedSemaphore | None = None
_slots_lock = threading.Lock()


def _semaphore() -> threading.BoundedSemaphore:
    """Process-wide limit on what-if requests running at once (each opens a DuckDB table)."""
    global _slots
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(get_settings().whatif_max_concurrent)
        return _slots


@contextmanager
def table_slot(busy_code: str = "what_if_busy") -> Iterator[None]:
    """Hold one of the process-wide DuckDB table slots, or raise 409 after WAIT_SECONDS."""
    slots = _semaphore()
    if not slots.acquire(timeout=WAIT_SECONDS):
        raise ConflictError(
            "Too many analysis requests are running; try again in a moment.", code=busy_code
        )
    try:
        yield
    finally:
        slots.release()


NOTE = (
    "Every result is a range under its listed assumptions, estimated from past sales; it is not "
    "a promise of what will happen."
)


def run_what_if(
    db: Session, tenant_id: uuid.UUID, version: DatasetVersion, request: WhatIfRequest
) -> dict[str, Any]:
    """Compute every scenario of the request on one version of the caller's tenant."""
    require_profile(version)
    config = merged_config(db, tenant_id, version, {})
    with table_slot(), analysis_context(db, tenant_id, version, config) as ctx:
        results = [run(ctx, Scenario(**s.model_dump())) for s in request.scenarios]
    return {
        "dataset_id": version.dataset_id,
        "dataset_version_id": version.id,
        "version": version.version,
        "scenarios": results,
        "note": NOTE,
    }
