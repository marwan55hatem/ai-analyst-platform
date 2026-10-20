"""Replay stored evidence SQL through SafeQueryService and compare it with what was shown."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services.query import SafeQueryService, validate_sql
from tests.ground_truth_fixtures import full
from tests.helpers import Account


def numbers_match(a: Any, b: Any) -> bool:
    """Compare two cells, numbers to the cent."""
    if isinstance(a, int | float) and isinstance(b, int | float):
        return float(a) == pytest.approx(float(b), abs=0.011)
    return bool(a == b)


def replay_evidence(
    client: TestClient,
    db: Session,
    owner: Account,
    run: dict[str, Any],
    insights: list[dict[str, Any]],
) -> int:
    """Validate and re-run every evidence query of the insights; return how many were checked."""
    version_id = uuid.UUID(run["dataset_version_id"])
    service = SafeQueryService(db)
    checked = 0
    for summary in insights:
        for query in full(client, owner, summary)["evidence"]["queries"]:
            validate_sql(query["sql"])  # the agent in Step 8 can run it
            result = service.run(uuid.UUID(owner.tenant_id), version_id, query["sql"], 5000)
            assert result.columns == query["columns"], query["purpose"]
            assert len(result.rows) == query["row_count"], query["purpose"]
            for shown, again in zip(query["rows"], result.rows, strict=False):
                assert all(numbers_match(x, y) for x, y in zip(shown, again, strict=True)), (
                    query["purpose"],
                    shown,
                    again,
                )
            checked += 1
    return checked
