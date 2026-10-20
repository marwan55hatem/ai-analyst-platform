"""The anomalies module on planted ground truth, inside the pipeline and through the API."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from tests.evidence_helpers import replay_evidence
from tests.ground_truth_fixtures import analyze, full, generator, of_type
from tests.helpers import API, register

MATCH_DAYS = 1
SHIFT_MATCH_DAYS = 7


def _planted(tmp_path: Path, seed: int, **options: Any) -> tuple[bytes, dict[str, Any]]:
    csv_path, _, facts = generator().generate(tmp_path, seed=seed, anomalies=True, **options)
    return csv_path.read_bytes(), facts


def _near(a: str, b: str, days: int) -> bool:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= days


@pytest.mark.parametrize("seed", [11, 5])
def test_planted_anomalies_are_found_with_recall_and_precision(
    client: TestClient, tmp_path: Path, seed: int
) -> None:
    owner = register(client)
    content, facts = _planted(tmp_path, seed)
    _, run, insights = analyze(client, owner, content)
    assert run["progress"]["modules"]["anomalies"]["status"] == "completed", run["progress"]
    summary = full(client, owner, of_type(insights, "anomaly_summary")[0])
    found = [e["start"] for e in summary["payload"]["events"]]
    planted = facts["planted"]["anomalies"]["points"]
    hits = [p for p in planted if any(_near(p["date"], f, MATCH_DAYS) for f in found)]
    true_found = [f for f in found if any(_near(p["date"], f, MATCH_DAYS) for p in planted)]
    recall = len(hits) / len(planted)
    precision = len(true_found) / len(found)
    assert recall >= 0.8, (found, planted)
    assert precision >= 0.6, (found, planted)
    for p in hits:
        event = next(e for e in summary["payload"]["events"] if _near(p["date"], e["start"], 1))
        assert event["kind"] == p["kind"]
    shift = facts["planted"]["anomalies"]["level_shift"]
    shifts = summary["payload"]["level_shifts"]
    assert any(
        s["kind"] == "level_shift_up" and _near(s["start"], shift["date"], SHIFT_MATCH_DAYS)
        for s in shifts
    ), shifts


def test_the_single_product_spike_names_that_product_first(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, facts = _planted(tmp_path, 11)
    _, _, insights = analyze(client, owner, content)
    cause = next(p for p in facts["planted"]["anomalies"]["points"] if p["cause_product"])
    event = next(
        full(client, owner, i)
        for i in of_type(insights, "anomaly")
        if _near(full(client, owner, i)["payload"]["start"], cause["date"], MATCH_DAYS)
    )
    products = event["payload"]["likely_contributors"]["product"]
    assert products[0]["item"] == cause["cause_product"]
    assert products[0]["share_of_change_pct"] > 50
    assert f"Likely contributors by product: {cause['cause_product']}" in event["summary"]
    assert "not proven causes" in event["summary"]
    purposes = [q["purpose"] for q in event["evidence"]["queries"]]
    assert "Likely contributors by product: during vs before" in purposes


def test_row_level_checks_count_the_planted_rows(client: TestClient, tmp_path: Path) -> None:
    owner = register(client)
    content, facts = _planted(tmp_path, 11)
    _, run, insights = analyze(client, owner, content)
    rows = full(client, owner, of_type(insights, "row_anomalies")[0])
    planted = facts["planted"]["anomalies"]
    counts = rows["payload"]["counts"]
    assert counts["zero_quantity"] == len(planted["zero_quantity_rows"])
    assert counts["unusual_discount"] == len(planted["unusual_discount_rows"])
    assert counts["extreme_price"] == len(planted["extreme_price_rows"])
    samples = rows["payload"]["samples"]
    assert (
        sorted(r["row_number"] for r in samples["extreme_price"]) == planted["extreme_price_rows"]
    )
    assert all(r["isolated_by_forest"] for r in samples["extreme_price"])
    assert [r["row_number"] for r in samples["zero_quantity"]] == planted["zero_quantity_rows"]
    assert rows["payload"]["dataset_version_number"] == 1
    assert rows["payload"]["dataset_version_id"] == run["dataset_version_id"]
    assert "stored data of version 1, not necessarily the row numbers" in rows["summary"]
    assert all("customer_id" not in r for s in samples.values() for r in s)


def test_anomalies_endpoint_lists_events_shifts_and_rows(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = _planted(tmp_path, 11)
    _, run, _ = analyze(client, owner, content)
    response = client.get(f"{API}/analysis-runs/{run['id']}/anomalies", headers=owner.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["skipped_reason"] is None
    assert len(body["events"]) >= 5
    assert body["level_shifts"]
    assert body["row_anomalies"]["counts"]["zero_quantity"] == 4
    assert {i["type"] for i in body["insights"]} >= {"anomaly", "anomaly_summary", "row_anomalies"}
    assert all("evidence" in i for i in body["insights"])


@pytest.mark.slow
def test_new_evidence_replays_on_planted_data_with_refunds(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = _planted(tmp_path, 11, segments=True, refunds=True)
    _, run, insights = analyze(client, owner, content)
    mine = [i for i in insights if i["module"] in {"anomalies", "segmentation"}]
    assert {i["type"] for i in mine} >= {
        "anomaly",
        "anomaly_summary",
        "level_shift",
        "row_anomalies",
        "rfm_segments",
        "customer_clusters",
    }
    assert replay_evidence(client, db, owner, run, mine) >= 15
    rows = full(client, owner, of_type(insights, "row_anomalies")[0])
    assert rows["payload"]["counts"]["returns"] == 25
    assert "treated as returns, not anomalies" in rows["summary"]


def test_limits_degrade_with_warnings_instead_of_failing(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "anomaly_row_sample", 500)
    monkeypatch.setattr(settings, "segment_max_customers", 150)
    owner = register(client)
    content, facts = _planted(tmp_path, 11, segments=True)
    _, run, insights = analyze(client, owner, content)
    assert run["progress"]["modules"]["anomalies"]["status"] == "completed"
    assert run["progress"]["modules"]["segmentation"]["status"] == "completed"
    rows = full(client, owner, of_type(insights, "row_anomalies")[0])
    assert rows["payload"]["forest"]["sampled"] is True
    assert "random sample of 500 rows" in rows["summary"]
    assert rows["payload"]["counts"]["extreme_price"] == 5  # counts still use every row
    warnings = run["progress"]["modules"]["segmentation"]["warnings"]
    assert any("fitted on a sample of 150" in w for w in warnings)
    page = client.get(f"{API}/analysis-runs/{run['id']}/segments", headers=owner.headers).json()
    assert page["assignments"]["total"] == facts["customer_count"]


def test_a_zero_time_budget_skips_the_heavy_steps_with_warnings(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "anomaly_max_seconds", 0.0)
    monkeypatch.setattr(settings, "segment_max_seconds", 0.0)
    owner = register(client)
    content, _ = _planted(tmp_path, 11, segments=True)
    _, run, insights = analyze(client, owner, content)
    anomalies = run["progress"]["modules"]["anomalies"]
    assert anomalies["status"] == "completed"
    assert any("row-level checks were skipped" in w for w in anomalies["warnings"])
    summary = full(client, owner, of_type(insights, "anomaly_summary")[0])
    assert summary["payload"]["forest"] is False
    assert summary["confidence"] == "low"
    assert "Isolation Forest check was skipped" in summary["summary"]
    groups = full(client, owner, of_type(insights, "customer_clusters")[0])
    assert groups["payload"]["k"] == 3
    assert groups["payload"]["stability"]["rounds"] == 0
    assert groups["confidence"] == "low"
    assert "time limit" in groups["summary"]
