"""The forecast module inside the analysis pipeline, and the custom forecast API."""

import csv
import io
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from mlflow.tracking import MlflowClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.ml.budget import Budget
from app.ml.forecasting.engine import forecast_series
from app.services.query import SafeQueryService, validate_sql
from tests.dataset_helpers import upload_ready
from tests.forecast_helpers import prepared
from tests.ground_truth_fixtures import analyze, full, generator, make, of_type
from tests.helpers import API, add_member, register


@pytest.fixture
def three_years(tmp_path: Path) -> tuple[bytes, dict[str, Any]]:
    csv_path, _, facts = generator().generate(tmp_path, years=3)
    return csv_path.read_bytes(), facts


def _forecasts(insights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [i for i in insights if i["module"] == "forecast"]


def _rewrite(content: bytes, transform: Any) -> bytes:
    rows = list(csv.reader(io.StringIO(content.decode())))
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(transform(rows))
    return out.getvalue().encode()


def test_the_automatic_run_forecasts_the_total_and_the_top_five_categories(
    client: TestClient, three_years: tuple[bytes, dict[str, Any]]
) -> None:
    owner = register(client)
    _, run, insights = analyze(client, owner, three_years[0])
    assert run["status"] == "completed", run["progress"]
    assert run["progress"]["modules"]["forecast"]["status"] == "completed"
    found = _forecasts(insights)
    assert len(of_type(found, "forecast_total")) == 1
    assert len(of_type(found, "forecast_group")) == 5
    total = full(client, owner, of_type(found, "forecast_total")[0])
    payload = total["payload"]
    assert payload["frequency"] == "month" and payload["horizon"] == 6
    assert payload["net_revenue_basis"] == "net"
    assert payload["periods"][0] == "2026-01-01"
    assert len(payload["point"]) == len(payload["lo80"]) == len(payload["hi95"]) == 6
    assert payload["winner"]["mase"] < payload["baseline"]["mase"]
    assert payload["winner"]["smape"] < 12.0
    assert total["confidence"] in ("high", "medium")
    assert total["title"].startswith("Net revenue is estimated at about ")
    assert "next month (80% range: " in total["title"] and "estimate" in total["summary"]
    chart = total["chart_spec"]
    assert chart["type"] == "forecast_band" and len(chart["forecast"]) == 6
    assert chart["history"] and set(chart["forecast"][0]) >= {"lo80", "hi80", "lo95", "hi95"}
    assert payload["mlflow"]["experiment"] == f"tenant-{owner.tenant_id}"


def _held_out(tmp_path: Path, years: int) -> tuple[list[float], list[float]]:
    facts = generator().generate(tmp_path, years=years)[2]
    holdout = str(facts["planted"]["holdout_year"])
    monthly = facts["monthly_revenue_cents"]
    train = [v / 100 for k, v in monthly.items() if not k.startswith(holdout)]
    held = [v / 100 for k, v in monthly.items() if k.startswith(holdout)]
    return train, held


def test_two_training_cycles_are_not_enough_so_the_forecast_says_so(tmp_path: Path) -> None:
    train, held = _held_out(tmp_path, 3)
    assert len(train) == 24 and len(held) == 12
    budget = Budget(600, 4000, rss_provider=lambda: 100.0)
    result = forecast_series(prepared(np.array(train)), 12, budget, 42)
    assert result.cycles == 2.0 and result.confidence == "low"  # honest: never high or medium
    assert result.no_better_than_baseline and result.model == "SeasonalNaive"


def test_the_forecast_recovers_the_planted_trend_and_season_of_the_held_out_year(
    tmp_path: Path,
) -> None:
    train, held = _held_out(tmp_path, 4)
    assert len(train) == 36 and len(held) == 12
    budget = Budget(600, 4000, rss_provider=lambda: 100.0)
    result = forecast_series(prepared(np.array(train)), 12, budget, 42)
    assert result.confidence in ("medium", "high") and not result.no_better_than_baseline
    errors = [abs(f - h) / h for f, h in zip(result.point, held, strict=True)]
    assert sum(errors) / 12 < 0.25
    assert sum(result.point) > sum(train[-12:])  # the planted upward trend is seen
    assert max(range(12), key=lambda i: result.point[i]) == 11  # December is the planted peak


def test_evidence_sql_of_every_forecast_insight_is_valid_and_reproducible(
    client: TestClient, db: Session, three_years: tuple[bytes, dict[str, Any]]
) -> None:
    owner = register(client)
    _, run, insights = analyze(client, owner, three_years[0])
    service = SafeQueryService(db)
    checked = 0
    for summary in _forecasts(insights):
        for query in full(client, owner, summary)["evidence"]["queries"]:
            validate_sql(query["sql"])
            result = service.run(
                uuid.UUID(owner.tenant_id), uuid.UUID(run["dataset_version_id"]), query["sql"], 5000
            )
            assert result.columns == query["columns"] and len(result.rows) == query["row_count"]
            checked += 1
    assert checked == 6


def test_run_forecast_endpoint_returns_the_forecast_insights(
    client: TestClient, three_years: tuple[bytes, dict[str, Any]]
) -> None:
    owner = register(client)
    _, run, _ = analyze(client, owner, three_years[0])
    body = client.get(f"{API}/analysis-runs/{run['id']}/forecast", headers=owner.headers).json()
    assert body["module_status"] == "completed" and len(body["forecasts"]) == 6
    assert body["forecasts"][0]["payload"]["point"]


def test_data_ending_mid_month_drops_the_partial_month_and_warns(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = make(tmp_path)

    def cut(rows: list[list[str]]) -> list[list[str]]:
        return [rows[0]] + [r for r in rows[1:] if r[1] <= "2024-12-14"]

    _, _, insights = analyze(client, owner, _rewrite(content, cut))
    total = full(client, owner, of_type(_forecasts(insights), "forecast_total")[0])
    assert total["payload"]["periods"][0] == "2024-12-01"  # December is forecast, not trained on
    assert any("part-way through Dec 2024" in w for w in total["payload"]["warnings"])
    assert "Dec 2024" in total["summary"]


def test_memory_pressure_skips_lightgbm_and_groups_but_the_run_still_completes(
    client: TestClient,
    three_years: tuple[bytes, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ml.modules import forecast as module

    monkeypatch.setattr(module, "Budget", lambda s, m: Budget(s, m, rss_provider=lambda: m + 500.0))
    owner = register(client)
    _, run, insights = analyze(client, owner, three_years[0])
    assert run["status"] == "completed"
    found = _forecasts(insights)
    assert len(found) == 1 and found[0]["type"] == "forecast_total"
    payload = full(client, owner, found[0])["payload"]
    assert "LightGBM" in payload["skipped_models"]
    assert any("memory limit" in w for w in payload["warnings"])
    state = run["progress"]["modules"]["forecast"]
    assert any("group forecast(s) were skipped" in w for w in state["warnings"])


def test_an_mlflow_outage_never_fails_the_run(
    client: TestClient,
    three_years: tuple[bytes, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "mlflow_tracking_uri", "http://127.0.0.1:9")
    owner = register(client)
    _, run, insights = analyze(client, owner, three_years[0])
    assert run["status"] == "completed" and len(_forecasts(insights)) == 6
    notes = run["progress"]["modules"]["forecast"]["warnings"]
    assert any("could not be recorded in MLflow" in n for n in notes)
    assert full(client, owner, _forecasts(insights)[0])["payload"]["mlflow"]["run_id"] is None


def test_a_run_is_recorded_in_mlflow_under_the_tenant_experiment(
    client: TestClient, three_years: tuple[bytes, dict[str, Any]]
) -> None:
    owner = register(client)
    _, run, insights = analyze(client, owner, three_years[0])
    payload = full(client, owner, _forecasts(insights)[0])["payload"]
    mlflow_client = MlflowClient(tracking_uri=get_settings().mlflow_tracking_uri)
    logged = mlflow_client.get_run(payload["mlflow"]["run_id"])
    assert logged.data.tags["tenant_id"] == owner.tenant_id
    assert logged.data.tags["dataset_version_id"] == run["dataset_version_id"]
    assert logged.data.params["horizon"] == "6" and logged.data.params["seed"] == "42"
    assert any(k.endswith(".mase") for k in logged.data.metrics)


def test_a_dataset_without_revenue_or_quantity_skips_the_forecast(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = make(tmp_path)

    def slim(rows: list[list[str]]) -> list[list[str]]:
        drop = {rows[0].index(c) for c in ("revenue", "quantity", "unit_price", "cost")}
        return [[v for i, v in enumerate(r) if i not in drop] for r in rows]

    _, run, insights = analyze(client, owner, _rewrite(content, slim))
    state = run["progress"]["modules"]["forecast"]
    assert state["status"] == "skipped" and "quantity or revenue" in state["reason"]
    assert _forecasts(insights) == []


# ---- custom forecast API ------------------------------------------------------------------------


def _upload(client: TestClient, owner: Any, content: bytes) -> str:
    return str(upload_ready(client, owner, content, "truth.csv")["id"])


def _post(client: TestClient, owner: Any, dataset_id: str, body: dict[str, Any]) -> Any:
    return client.post(f"{API}/datasets/{dataset_id}/forecast", headers=owner.headers, json=body)


def _poll(client: TestClient, owner: Any, run_id: str) -> dict[str, Any]:
    response = client.get(f"{API}/forecast-runs/{run_id}", headers=owner.headers)
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def test_custom_forecast_is_queued_then_polled_and_grouped(
    client: TestClient, three_years: tuple[bytes, dict[str, Any]]
) -> None:
    owner = register(client)
    dataset_id = _upload(client, owner, three_years[0])
    queued = _post(
        client, owner, dataset_id, {"metric": "quantity", "horizon": 3, "group_by": "category"}
    )
    assert queued.status_code == 202, queued.text
    polled = _poll(client, owner, queued.json()["id"])
    assert polled["status"] == "completed" and polled["error"] is None
    insights = polled["result"]["insights"]
    assert len(insights) == 6 and insights[0]["type"] == "forecast_total"
    assert len(insights[0]["payload"]["point"]) == 3
    assert insights[0]["title"].startswith("Quantity sold is estimated at about ")
    assert {i["payload"]["group"] for i in insights[1:]} == {
        "Electronics",
        "Furniture",
        "Office",
        "Grocery",
        "Apparel",
    }


def test_the_request_only_queues_the_work_for_the_worker(
    client: TestClient,
    three_years: tuple[bytes, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import tasks

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        tasks.run_forecast_task, "delay", lambda tenant, run: sent.append((tenant, run))
    )
    owner = register(client)
    dataset_id = _upload(client, owner, three_years[0])
    queued = _post(client, owner, dataset_id, {})
    assert queued.status_code == 202 and queued.json()["status"] == "pending"
    assert queued.json()["result"] == {}  # nothing was computed in the request
    assert sent == [(owner.tenant_id, queued.json()["id"])]  # tenant comes from the JWT
    again = _post(client, owner, dataset_id, {})
    assert again.status_code == 409 and again.json()["code"] == "forecast_running"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"metric": "price"}, None),
        ({"metric": "profit"}, None),
        ({"horizon": 0}, None),
        ({"horizon": 500}, None),
        ({"horizon": 25}, "horizon_too_large"),
        ({"max_groups": 11, "group_by": "category"}, "too_many_groups"),
        ({"group_by": "order_id"}, "invalid_group_by"),
        ({"group_by": "revenue"}, "invalid_group_by"),
        ({"group_by": "customer_id"}, "invalid_group_by"),
        ({"frequency": "hour"}, None),
    ],
)
def test_invalid_requests_are_rejected_with_422(
    client: TestClient, tmp_path: Path, body: dict[str, Any], code: str | None
) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    response = _post(client, owner, _upload(client, owner, content), body)
    assert response.status_code == 422, (body, response.text)
    if code:
        assert response.json()["code"] == code


def test_a_metric_the_dataset_lacks_is_rejected(client: TestClient, tmp_path: Path) -> None:
    owner = register(client)
    content, _ = make(tmp_path)

    def no_cost(rows: list[list[str]]) -> list[list[str]]:
        keep = [i for i, h in enumerate(rows[0]) if h != "cost"]
        return [[r[i] for i in keep] for r in rows]

    dataset_id = _upload(client, owner, _rewrite(content, no_cost))
    response = _post(client, owner, dataset_id, {"metric": "cost"})
    assert response.status_code == 422 and response.json()["code"] == "metric_unavailable"


def test_any_role_in_the_tenant_may_request_a_forecast(
    client: TestClient, three_years: tuple[bytes, dict[str, Any]]
) -> None:
    owner = register(client)
    member = add_member(client, owner, "member")
    dataset_id = _upload(client, owner, three_years[0])
    response = _post(client, member, dataset_id, {"horizon": 2})
    assert response.status_code == 202
    assert _poll(client, owner, response.json()["id"])["status"] == "completed"


def test_a_tenant_id_in_the_body_is_not_trusted(
    client: TestClient, three_years: tuple[bytes, dict[str, Any]], db: Session
) -> None:
    from app.models.model_run import ModelRun

    owner = register(client)
    other = register(client)
    dataset_id = _upload(client, owner, three_years[0])
    response = _post(client, owner, dataset_id, {"tenant_id": other.tenant_id, "horizon": 2})
    assert response.status_code == 202
    row = db.get(ModelRun, uuid.UUID(response.json()["id"]))
    assert row is not None and str(row.tenant_id) == owner.tenant_id


def test_a_failing_forecast_is_recorded_as_failed_with_a_plain_message(
    client: TestClient,
    three_years: tuple[bytes, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import forecasting

    def boom(self: object, ctx: object) -> list[object]:
        raise RuntimeError("internal detail that must not leak")

    monkeypatch.setattr(forecasting.ForecastModule, "run", boom)
    owner = register(client)
    dataset_id = _upload(client, owner, three_years[0])
    queued = _post(client, owner, dataset_id, {})
    polled = _poll(client, owner, queued.json()["id"])
    assert polled["status"] == "failed"
    assert polled["error"] == forecasting.FORECAST_FAILED_MESSAGE
    assert "internal detail" not in str(polled)


def test_daily_frequency_can_be_requested_for_a_short_daily_file(
    client: TestClient, tmp_path: Path
) -> None:
    csv_path, _, facts = generator().generate_daily(tmp_path)
    owner = register(client)
    dataset_id = _upload(client, owner, csv_path.read_bytes())
    queued = _post(client, owner, dataset_id, {"frequency": "day", "horizon": 7})
    polled = _poll(client, owner, queued.json()["id"])
    assert polled["status"] == "completed", polled
    (total,) = polled["result"]["insights"]
    payload = total["payload"]
    assert payload["frequency"] == "day" and len(payload["point"]) == 7
    assert (
        payload["periods"][0] == (date.fromisoformat(facts["last_date"]) + timedelta(1)).isoformat()
    )
    assert payload["winner"]["smape"] < 12.0


def test_history_behind_the_forecast_equals_the_independently_computed_monthly_net_revenue(
    client: TestClient, three_years: tuple[bytes, dict[str, Any]]
) -> None:
    owner = register(client)
    _, _, insights = analyze(client, owner, three_years[0])
    total = full(client, owner, of_type(_forecasts(insights), "forecast_total")[0])
    truth = three_years[1]["monthly_revenue_cents"]
    shown = {h["period"][:7]: h["value"] for h in total["chart_spec"]["history"]}
    assert set(shown) == set(truth)
    for month, cents in truth.items():
        assert round(shown[month] * 100) == cents, month
