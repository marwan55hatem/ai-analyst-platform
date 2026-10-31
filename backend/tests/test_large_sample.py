"""End to end on sales_large_clean.csv (309,254 rows) against its answer key.

The CSV is 25 MB and kept out of the repository, so these tests run where the file has been
copied to evaluation/datasets/samples/ and skip elsewhere; the answer key is committed.
"""

import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.ground_truth_fixtures import analyze, full, of_type
from tests.helpers import API, register

SAMPLES = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "samples"
DATA = SAMPLES / "sales_large_clean.csv"
FACTS = SAMPLES / "sales_large_clean_facts.json"
MAX_FLAGGED = 15
MATCH_DAYS = 1
MAX_TIMESERIES_SECONDS = 2.0

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not DATA.exists(), reason=f"{DATA.name} is not in the samples folder"),
]


def _near(a: str, b: str) -> bool:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= MATCH_DAYS


def _kpi(body: dict[str, Any], key: str) -> Any:
    return next(k for k in body["kpis"] if k["key"] == key)["value"]


def test_the_large_sample_matches_its_answer_key(client: TestClient) -> None:
    facts = json.loads(FACTS.read_text(encoding="utf-8"))
    owner = register(client)
    dataset_id, run, insights = analyze(client, owner, DATA.read_bytes())
    assert run["status"] == "completed", run["progress"]

    # The timeseries total equals the answer key and the dashboard's own KPI request.
    url = f"{API}/datasets/{dataset_id}/timeseries"
    started = time.perf_counter()
    whole = client.get(url, params={"grain": "day"}, headers=owner.headers).json()
    seconds = time.perf_counter() - started
    print(f"\ntimeseries over {whole['sample_size']:,} rows: {seconds * 1000:.0f} ms")
    assert seconds < MAX_TIMESERIES_SECONDS
    assert whole["range"] == {"start": facts["date_range"][0], "end": facts["date_range"][1]}
    assert _kpi(whole, "net_revenue") == facts["net_revenue"] == 89601650.37
    assert sum(p["net_revenue"] for p in whole["series"]) == pytest.approx(
        facts["net_revenue"], abs=0.01
    )
    monthly = client.get(url, params={"grain": "month"}, headers=owner.headers).json()
    assert _kpi(monthly, "net_revenue") == facts["net_revenue"]
    assert _kpi(whole, "orders") == facts["orders"]

    # Every planted day is found, nearly nothing else is.
    summary = full(client, owner, of_type(insights, "anomaly_summary")[0])
    found = summary["payload"]["events"]
    planted = [p["date"] for p in facts["planted_anomaly_days"]]
    hits = [p for p in planted if any(_near(p, e["start"]) for e in found)]
    correct = [e for e in found if any(_near(p, e["start"]) for p in planted)]
    recall, precision = len(hits) / len(planted), len(correct) / len(found)
    print(f"anomalies: {len(found)} flagged, precision {precision:.2f}, recall {recall:.2f}")
    assert recall == 1.0, found
    assert len(found) < MAX_FLAGGED, found
    assert precision >= 0.8, found

    # No impossible outlier bounds, and a realistic share of flagged values.
    profile = client.get(f"{API}/datasets/{dataset_id}/profile", headers=owner.headers).json()
    columns = {c["name"]: c["stats"] for c in profile["columns"]}
    for name in ("unit_price", "revenue", "cost"):
        bounds = columns[name]["outlier_bounds"]
        share = columns[name]["outlier_count"] / facts["rows"]
        print(f"outliers in {name}: {share:.2%} outside {bounds['lower']} to {bounds['upper']}")
        assert bounds["lower"] > 0 and share < 0.02

    # One duplicate row in 309k leaves the figures at high confidence on every card.
    assert whole["confidence"] == "high"
    for kind in ("kpi_summary", "pareto", "seasonality"):
        assert of_type(insights, kind)[0]["confidence"] == "high", kind

    # The forecast compares models instead of repeating last year.
    total = full(client, owner, of_type(insights, "forecast_total")[0])
    payload = total["payload"]
    print(
        f"forecast: {payload['model']}, MASE {payload['winner']['mase']}, "
        f"baseline {payload['baseline']['mase']}, first month {payload['point'][0]:,.2f}"
    )
    assert len(payload["scores"]) == 5
    assert payload["model"] != "SeasonalNaive"
    history = {p["period"]: p["value"] for p in total["chart_spec"]["history"]}
    assert payload["point"][0] > history["2026-09-01"]  # October is seasonally stronger
