"""MLflow logging: per-tenant experiment, tags, metrics, JSON artifact; an outage never hurts."""

import json
import time
import uuid
from pathlib import Path

import pytest
from mlflow.tracking import MlflowClient

from app.ml.forecasting.budget import Budget
from app.ml.forecasting.engine import forecast_series
from app.ml.forecasting.tracking import (
    experiment_name,
    forecast_payload,
    log_forecast_run,
    metric_items,
)
from tests.forecast_helpers import monthly_values, prepared


@pytest.fixture(scope="module")
def results():  # type: ignore[no-untyped-def]
    budget = Budget(600, 4000, rss_provider=lambda: 100.0)
    return [
        forecast_series(prepared(monthly_values(40, 1), name="Total"), 6, budget, 42),
        forecast_series(prepared(monthly_values(40, 2), name="Office"), 6, budget, 42),
    ]


def test_a_run_is_logged_with_tags_params_metrics_and_a_forecast_artifact(
    tmp_path: Path,
    results,  # type: ignore[no-untyped-def]
) -> None:
    uri = (tmp_path / "mlruns").as_uri()
    tenant, version = uuid.uuid4(), uuid.uuid4()
    outcome = log_forecast_run(uri, tenant, version, {"metric": "revenue", "horizon": 6}, results)
    assert outcome.warning is None and outcome.run_id
    assert outcome.experiment == experiment_name(tenant) == f"tenant-{tenant}"

    client = MlflowClient(tracking_uri=uri)
    experiment = client.get_experiment_by_name(f"tenant-{tenant}")
    assert experiment is not None
    run = client.get_run(outcome.run_id)
    assert run.data.tags["tenant_id"] == str(tenant)
    assert run.data.tags["dataset_version_id"] == str(version)
    assert run.data.params == {"metric": "revenue", "horizon": "6"}
    assert "s0.SeasonalNaive.mase" in run.data.metrics and "s1.AutoETS.smape" in run.data.metrics
    assert run.info.status == "FINISHED"

    local = client.download_artifacts(outcome.run_id, "forecast.json")
    saved = json.loads(Path(local).read_text(encoding="utf-8"))
    assert [s["name"] for s in saved["series"]] == ["Total", "Office"]
    assert len(saved["series"][0]["point"]) == 6
    flat = json.dumps(saved)
    assert "history" not in flat and "rows" not in saved["series"][0]  # never raw data


def test_a_second_run_for_the_same_tenant_reuses_the_experiment(
    tmp_path: Path,
    results,  # type: ignore[no-untyped-def]
) -> None:
    uri = (tmp_path / "mlruns").as_uri()
    tenant = uuid.uuid4()
    first = log_forecast_run(uri, tenant, uuid.uuid4(), {"a": 1}, results)
    second = log_forecast_run(uri, tenant, uuid.uuid4(), {"a": 2}, results)
    assert first.run_id != second.run_id
    client = MlflowClient(tracking_uri=uri)
    experiment = client.get_experiment_by_name(experiment_name(tenant))
    assert experiment is not None
    assert len(client.search_runs([experiment.experiment_id])) == 2


def test_two_tenants_get_separate_experiments(tmp_path: Path, results) -> None:  # type: ignore[no-untyped-def]
    uri = (tmp_path / "mlruns").as_uri()
    a, b = uuid.uuid4(), uuid.uuid4()
    log_forecast_run(uri, a, uuid.uuid4(), {}, results)
    log_forecast_run(uri, b, uuid.uuid4(), {}, results)
    client = MlflowClient(tracking_uri=uri)
    names = {e.name for e in client.search_experiments()}
    assert {experiment_name(a), experiment_name(b)} <= names


def test_an_unreachable_server_gives_a_warning_quickly_and_never_raises(results) -> None:  # type: ignore[no-untyped-def]
    started = time.monotonic()
    outcome = log_forecast_run("http://127.0.0.1:9", uuid.uuid4(), uuid.uuid4(), {}, results)
    assert time.monotonic() - started < 12
    assert outcome.run_id is None
    assert outcome.warning and "could not be recorded in MLflow" in outcome.warning
    assert "unaffected" in outcome.warning


def test_a_hanging_server_is_abandoned_after_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
    results,  # type: ignore[no-untyped-def]
) -> None:
    from app.ml.forecasting import tracking

    monkeypatch.setattr(tracking, "_log", lambda *a, **k: time.sleep(5))
    started = time.monotonic()
    outcome = log_forecast_run("http://x", uuid.uuid4(), uuid.uuid4(), {}, results, deadline=0.3)
    assert time.monotonic() - started < 2
    assert outcome.warning and "did not answer in time" in outcome.warning


def test_metric_keys_never_contain_group_names(results) -> None:  # type: ignore[no-untyped-def]
    keys = [k for k, _ in metric_items(results)]
    assert keys and all(k.startswith(("s0.", "s1.")) for k in keys)
    assert not any("Office" in k for k in keys)
    assert forecast_payload(results)["series"][1]["name"] == "Office"
