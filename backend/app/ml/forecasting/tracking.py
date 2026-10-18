"""MLflow logging for forecast runs. It can never fail or noticeably slow a forecast.

One MLflow run per forecast run, in the experiment `tenant-<tenant_id>`, tagged with the tenant
and dataset version. Parameters, per-model backtest metrics and the forecast itself (periods,
point and interval bounds only, as a JSON artifact) are logged. Raw rows are never logged.
The whole call runs on a daemon thread that is abandoned after a short deadline.
"""

import json
import os
import threading
import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from app.ml.forecasting.engine import ForecastResult

log = structlog.get_logger()
DEADLINE_SECONDS = 8.0
MAX_PARAM_LENGTH = 400


@dataclass
class TrackingOutcome:
    """Where the run was logged, or the warning explaining why it was not."""

    run_id: str | None = None
    experiment: str | None = None
    warning: str | None = None


def experiment_name(tenant_id: uuid.UUID | str) -> str:
    """Return the per-tenant experiment name."""
    return f"tenant-{tenant_id}"


def forecast_payload(results: list[ForecastResult]) -> dict[str, Any]:
    """Return the JSON artifact: forecasts and backtest scores, never the input rows."""
    return {
        "series": [
            {
                "name": r.series,
                "model": r.model,
                "confidence": r.confidence,
                "no_better_than_baseline": r.no_better_than_baseline,
                "periods": [d.isoformat() for d in r.periods],
                "point": r.point,
                "lo80": r.lo80,
                "hi80": r.hi80,
                "lo95": r.lo95,
                "hi95": r.hi95,
                "scores": [s.as_dict() for s in r.scores],
                "warnings": r.warnings,
            }
            for r in results
        ]
    }


def metric_items(results: list[ForecastResult]) -> list[tuple[str, float]]:
    """Return (key, value) pairs; series are numbered so group names never reach metric keys."""
    items: list[tuple[str, float]] = []
    for i, r in enumerate(results):
        for s in r.scores:
            for key, value in (
                ("smape", s.smape),
                ("mase", s.mase),
                ("mae", s.mae),
                ("coverage80", s.coverage80),
            ):
                if value is not None and value == value and abs(value) != float("inf"):
                    items.append((f"s{i}.{s.name}.{key}", float(value)))
    return items


def log_forecast_run(
    tracking_uri: str,
    tenant_id: uuid.UUID | str,
    dataset_version_id: uuid.UUID | str,
    params: dict[str, Any],
    results: list[ForecastResult],
    deadline: float = DEADLINE_SECONDS,
) -> TrackingOutcome:
    """Log a forecast run to MLflow; on any problem return a warning instead of raising."""
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "3")
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    os.environ.setdefault("MLFLOW_ARTIFACT_UPLOAD_DOWNLOAD_TIMEOUT", "5")
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["outcome"] = _log(tracking_uri, tenant_id, dataset_version_id, params, results)
        except Exception as exc:
            box["error"] = type(exc).__name__

    thread = threading.Thread(target=work, daemon=True, name="mlflow-logger")
    thread.start()
    thread.join(deadline)
    if "outcome" in box:
        outcome: TrackingOutcome = box["outcome"]
        return outcome
    reason = box.get("error", "it did not answer in time")
    log.warning("mlflow_logging_failed", reason=reason)
    return TrackingOutcome(
        warning="The forecast could not be recorded in MLflow "
        f"({reason}); the forecast itself is unaffected."
    )


def _log(
    tracking_uri: str,
    tenant_id: uuid.UUID | str,
    dataset_version_id: uuid.UUID | str,
    params: dict[str, Any],
    results: list[ForecastResult],
) -> TrackingOutcome:
    from mlflow.entities import Metric, Param, RunTag
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri)
    name = experiment_name(tenant_id)
    experiment = client.get_experiment_by_name(name)
    experiment_id = experiment.experiment_id if experiment else client.create_experiment(name)
    run = client.create_run(
        experiment_id,
        tags={"tenant_id": str(tenant_id), "dataset_version_id": str(dataset_version_id)},
    )
    run_id = run.info.run_id
    try:
        metrics = [Metric(k, v, 0, 0) for k, v in metric_items(results)]  # type: ignore[no-untyped-call]
        logged = [Param(k, str(v)[:MAX_PARAM_LENGTH]) for k, v in params.items()]  # type: ignore[no-untyped-call]
        tags = [RunTag("series_count", str(len(results)))]  # type: ignore[no-untyped-call]
        client.log_batch(run_id, metrics=metrics, params=logged, tags=tags)
        client.log_text(run_id, json.dumps(forecast_payload(results)), "forecast.json")
        client.set_terminated(run_id, "FINISHED")
    except Exception:
        client.set_terminated(run_id, "FAILED")
        raise
    return TrackingOutcome(run_id=run_id, experiment=name)
