"""Dashboard endpoints through the API: time series, table sorting and CSV exports."""

import csv
import io
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services.exports import safe_cell
from tests.dataset_helpers import upload_ready
from tests.ground_truth_fixtures import analyze
from tests.helpers import API, register
from tests.ml_context import generate, read_rows

pytestmark = pytest.mark.slow


def _revenue_by_day(csv_path: Path) -> dict[date, float]:
    totals: dict[date, float] = defaultdict(float)
    for row in read_rows(csv_path):
        totals[date.fromisoformat(row["order_date"])] += float(row["revenue"])
    return totals


def test_time_series_matches_the_ground_truth_and_is_tenant_scoped(
    client: TestClient, tmp_path: Path
) -> None:
    owner, stranger = register(client), register(client)
    csv_path, _ = generate(tmp_path)
    dataset = upload_ready(client, owner, csv_path.read_bytes(), "truth.csv")
    by_day = _revenue_by_day(csv_path)
    url = f"{API}/datasets/{dataset['id']}/timeseries"

    found = client.get(
        url, params={"start": "2024-03-01", "end": "2024-03-31"}, headers=owner.headers
    )
    assert found.status_code == 200, found.text
    body = found.json()
    march = sum(v for d, v in by_day.items() if date(2024, 3, 1) <= d <= date(2024, 3, 31))
    before = sum(v for d, v in by_day.items() if date(2024, 1, 30) <= d <= date(2024, 2, 29))
    revenue = next(k for k in body["kpis"] if k["key"] == "net_revenue")
    assert revenue["value"] == pytest.approx(march, abs=0.01)
    assert revenue["previous_value"] == pytest.approx(before, abs=0.01)
    assert body["previous_range"] == {"start": "2024-01-30", "end": "2024-02-29"}
    assert len(body["series"]) == 31 and body["confidence"] in ("high", "medium", "low")
    assert sum(p["net_revenue"] for p in body["series"]) == pytest.approx(march, abs=0.01)
    assert body["evidence"] and body["evidence"][0]["sql"].startswith("SELECT")

    weekly = client.get(url, params={"grain": "week"}, headers=owner.headers).json()
    assert weekly["grain"] == "week" and len(weekly["series"]) > 100

    assert client.get(url, headers=stranger.headers).status_code == 404
    bad = client.get(
        url, params={"start": "2024-03-10", "end": "2024-03-01"}, headers=owner.headers
    )
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_range"
    assert client.get(url, params={"grain": "hour"}, headers=owner.headers).status_code == 422
    far = client.get(url, params={"start": "2030-01-01"}, headers=owner.headers)
    assert far.status_code == 422 and far.json()["code"] == "range_outside_data"


def _csv_rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_churn_and_segment_tables_sort_and_export_csv(client: TestClient, tmp_path: Path) -> None:
    owner, stranger = register(client), register(client)
    csv_path, _ = generate(tmp_path, churn=True)
    _, run, _ = analyze(client, owner, csv_path.read_bytes())
    churn_url = f"{API}/analysis-runs/{run['id']}/churn"

    by_stake = client.get(
        churn_url,
        params={"sort": "revenue_at_stake", "order": "desc", "limit": 50},
        headers=owner.headers,
    )
    assert by_stake.status_code == 200, by_stake.text
    stakes = [i["revenue_at_stake"] for i in by_stake.json()["customers"]["items"]]
    assert stakes == sorted(stakes, reverse=True) and len(stakes) == 50
    recent = client.get(
        churn_url, params={"sort": "recency_days", "order": "asc"}, headers=owner.headers
    ).json()["customers"]["items"]
    assert [i["recency_days"] for i in recent] == sorted(i["recency_days"] for i in recent)
    refused = client.get(churn_url, params={"sort": "customer_key; drop"}, headers=owner.headers)
    assert refused.status_code == 422

    export = client.get(
        f"{churn_url}.csv",
        params={"risk_band": "high", "sort": "expected_loss"},
        headers=owner.headers,
    )
    assert export.status_code == 200, export.text
    assert export.headers["content-type"].startswith("text/csv")
    assert "attachment" in export.headers["content-disposition"]
    rows = _csv_rows(export.text)
    high = client.get(churn_url, params={"risk_band": "high"}, headers=owner.headers).json()
    assert len(rows) == high["customers"]["total"] and {r["risk_band"] for r in rows} <= {"high"}
    losses = [float(r["expected_loss"]) for r in rows]
    assert losses == sorted(losses, reverse=True)
    assert rows[0]["reason_1"] and set(rows[0]) >= {"customer_key", "probability", "reason_3"}

    segments_url = f"{API}/analysis-runs/{run['id']}/segments"
    rich = client.get(
        segments_url, params={"sort": "monetary", "order": "desc"}, headers=owner.headers
    ).json()
    assert rich["module_status"] == "completed", rich["skipped_reason"]
    grid = rich["rfm_grid"]
    assert sum(c["customers"] for c in grid) == rich["assignments"]["total"]
    assert {(c["r_score"], c["f_score"]) for c in grid} <= {
        (r, f) for r in range(1, 6) for f in range(1, 6)
    }
    spend = [i["monetary"] for i in rich["assignments"]["items"]]
    assert spend == sorted(spend, reverse=True)
    exported = _csv_rows(client.get(f"{segments_url}.csv", headers=owner.headers).text)
    assert len(exported) == rich["assignments"]["total"]
    assert [r["customer_key"] for r in exported] == sorted(r["customer_key"] for r in exported)

    assert client.get(f"{churn_url}.csv", headers=stranger.headers).status_code == 404
    assert client.get(f"{segments_url}.csv", headers=stranger.headers).status_code == 404


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=HYPERLINK(1)", "'=HYPERLINK(1)"),
        ("+1", "'+1"),
        ("-2+3", "'-2+3"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tx", "'\tx"),
        ("C-0042", "C-0042"),
        (-12.5, -12.5),
        (None, None),
    ],
)
def test_text_that_looks_like_a_formula_is_neutralised(value: Any, expected: Any) -> None:
    assert safe_cell(value) == expected
