"""The run summary: figures a finished run keeps for alerts and reports, equal to the dashboard."""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.analysis import AnalysisRun
from app.models.dataset import DatasetVersion
from app.services.run_summary import from_profile_last_date, latest_complete_month
from tests.ground_truth_fixtures import analyze, make
from tests.helpers import API, register


def _summary(db: Session, run_id: str) -> dict[str, Any]:
    run = db.get(AnalysisRun, run_id)
    assert run is not None and run.summary is not None
    return run.summary


@pytest.mark.slow
def test_summary_matches_the_dashboard_and_the_planted_facts(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    owner = register(client)
    content, facts = make(tmp_path)
    dataset_id, run, _ = analyze(client, owner, content)
    summary = _summary(db, run["id"])

    end = date.fromisoformat(facts["last_date"])
    start = end - timedelta(days=89)
    dashboard = client.get(
        f"{API}/datasets/{dataset_id}/timeseries",
        params={"start": start.isoformat(), "end": end.isoformat(), "grain": "day"},
        headers=owner.headers,
    ).json()
    assert summary["kpis"]["range"] == dashboard["range"]
    assert summary["kpis"]["previous_range"] == dashboard["previous_range"]
    # Parallel DuckDB sums may differ in the last float bits; compare at display precision.
    for mine, card in zip(summary["kpis"]["kpis"], dashboard["kpis"], strict=True):
        assert {k: v for k, v in mine.items() if not isinstance(v, float)} == {
            k: v for k, v in card.items() if not isinstance(v, float)
        }
        for field in ("value", "previous_value", "change"):
            if isinstance(mine[field], float):
                assert mine[field] == pytest.approx(card[field], rel=1e-9, abs=0.005)

    months = {m["period"]: m for m in summary["monthly"]}
    last = summary["latest_month"]
    assert last["period"] == "2024-12-01" and not last["partial"]
    planted = facts["monthly_revenue_cents"]["2024-12"]
    assert round(months["2024-12-01"]["net_revenue"] * 100) == planted
    assert summary["last_data_date"] == facts["last_date"]

    forecast = summary["forecast"]
    assert forecast is not None and forecast["metric"] == "revenue"
    assert forecast["made_through"] == "2024-12-01"
    assert min(forecast["periods"]) == "2025-01-01"
    first = forecast["periods"]["2025-01-01"]
    assert first["lo95"] <= first["lo80"] <= first["point"] <= first["hi80"] <= first["hi95"]

    assert summary["dead_stock"]["names"] == facts["dead_stock"]
    version = db.get(DatasetVersion, run["dataset_version_id"])
    assert version is not None and summary["quality_score"] == version.quality_score
    assert summary["version"] == 1 and summary["schema"] == 1


def test_latest_complete_month_skips_partial_months() -> None:
    monthly = [
        {"period": "2024-01-01", "partial": True},
        {"period": "2024-02-01", "partial": False},
        {"period": "2024-03-01", "partial": True},
    ]
    assert latest_complete_month(monthly) == monthly[1]
    assert latest_complete_month([{"period": "2024-01-01", "partial": True}]) is None


def test_last_date_from_profile_tolerates_missing_and_bad_values() -> None:
    version = DatasetVersion(dataset_profile={"date_range": {"max": "2026-09-30T00:00:00"}})
    assert from_profile_last_date(version) == date(2026, 9, 30)
    assert from_profile_last_date(DatasetVersion(dataset_profile=None)) is None
    bad = DatasetVersion(dataset_profile={"date_range": {"max": "not a date"}})
    assert from_profile_last_date(bad) is None
