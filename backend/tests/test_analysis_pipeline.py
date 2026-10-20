"""Pipeline behaviour: evidence, capabilities, failure isolation, re-runs, chaining, sweeper."""

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.ml import registry
from app.ml.base import AnalysisContext, AnalysisModule, Insight
from app.models.analysis import AnalysisRun, AnalysisStatus
from app.models.analysis import Insight as InsightRow
from app.models.dataset import DatasetVersion
from app.services.analysis import ANALYSIS_STUCK_ERROR
from app.services.datasets import fail_stuck_ingestions
from tests.dataset_helpers import upload, upload_ready
from tests.evidence_helpers import replay_evidence
from tests.ground_truth_fixtures import analyze, full, make, of_type
from tests.helpers import API, add_member, register

FROZEN_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def drop_columns(content: bytes, names: set[str]) -> bytes:
    """Return the CSV without the named columns."""
    rows = list(csv.reader(io.StringIO(content.decode("utf-8"))))
    keep = [i for i, h in enumerate(rows[0]) if h not in names]
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows([[r[i] for i in keep] for r in rows])
    return out.getvalue().encode("utf-8")


def add_column(content: bytes, name: str, make_value: Any) -> bytes:
    """Return the CSV with an extra column computed from each row (a dict)."""
    rows = list(csv.reader(io.StringIO(content.decode("utf-8"))))
    header, body = rows[0], rows[1:]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([*header, name])
    for r in body:
        writer.writerow([*r, make_value(dict(zip(header, r, strict=True)))])
    return out.getvalue().encode("utf-8")


def test_every_evidence_query_is_valid_and_reproducible(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = make(tmp_path, refunds=True)
    dataset_id, run, insights = analyze(client, owner, content)
    modules = {i["module"] for i in insights}
    assert modules == {
        "kpis",
        "trend_seasonality",
        "pareto",
        "cohort_retention",
        "forecast",
        "anomalies",
        "segmentation",
    }
    checked = replay_evidence(client, db, owner, run, insights)
    assert checked >= 10


def test_capabilities_for_a_full_dataset_and_one_without_customer_id(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    full_set = upload_ready(client, owner, content, "full.csv")
    caps = client.get(f"{API}/datasets/{full_set['id']}/capabilities", headers=owner.headers).json()
    assert {m["name"]: m["available"] for m in caps["modules"]} == {
        "kpis": True,
        "trend_seasonality": True,
        "pareto": True,
        "cohort_retention": True,
        "forecast": True,
        "anomalies": True,
        "segmentation": True,
    }

    slim = upload_ready(client, owner, drop_columns(content, {"customer_id"}), "slim.csv")
    caps = client.get(f"{API}/datasets/{slim['id']}/capabilities", headers=owner.headers).json()
    cohort = next(m for m in caps["modules"] if m["name"] == "cohort_retention")
    assert cohort["available"] is False
    assert cohort["reason"] == "no customer_id column, so cohort retention is skipped"
    assert next(m for m in caps["modules"] if m["name"] == "kpis")["available"] is True


def test_dataset_without_customer_id_still_completes(client: TestClient, tmp_path: Path) -> None:
    owner = register(client)
    content, facts = make(tmp_path)
    _, run, insights = analyze(client, owner, drop_columns(content, {"customer_id"}))

    assert run["status"] == "completed_with_warnings"
    cohort = run["progress"]["modules"]["cohort_retention"]
    assert cohort["status"] == "skipped" and "customer_id" in cohort["reason"]
    assert "cohort_retention" not in {i["module"] for i in insights}
    kpi = full(client, owner, of_type(insights, "kpi_summary")[0])
    assert round(kpi["payload"]["net_revenue"] * 100) == facts["net_revenue_cents"]


def test_dataset_without_revenue_derives_it_from_quantity_and_price(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, facts = make(tmp_path)
    _, run, insights = analyze(client, owner, drop_columns(content, {"revenue"}))

    assert run["status"] == "completed", run["progress"]
    kpi = full(client, owner, of_type(insights, "kpi_summary")[0])
    assert kpi["payload"]["derived_revenue"] is True
    assert round(kpi["payload"]["net_revenue"] * 100) == facts["net_revenue_cents"]
    assert "derived as quantity x price" in kpi["summary"]
    dead = full(client, owner, of_type(insights, "dead_stock")[0])
    assert sorted(dead["payload"]["item_names"]) == facts["dead_stock"]


def test_without_revenue_quantity_or_price_the_revenue_modules_are_skipped(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    slim = drop_columns(content, {"revenue", "unit_price", "cost"})
    dataset = upload_ready(client, owner, slim, "slim.csv")
    caps = client.get(f"{API}/datasets/{dataset['id']}/capabilities", headers=owner.headers).json()
    kpis = next(m for m in caps["modules"] if m["name"] == "kpis")
    assert kpis["available"] is False and "no revenue column" in kpis["reason"]


def test_date_column_is_chosen_by_name_and_can_be_overridden(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    with_ship = add_column(content, "ship_date", lambda r: r["order_date"])
    dataset = upload_ready(client, owner, with_ship, "two-dates.csv")
    url = f"{API}/datasets/{dataset['id']}/capabilities"
    default = client.get(url, headers=owner.headers).json()
    assert default["roles"]["columns"]["date"] == "order_date"
    assert set(default["roles"]["date_candidates"]) == {"order_date", "ship_date"}
    overridden = client.get(f"{url}?date_column=ship_date", headers=owner.headers).json()
    assert overridden["roles"]["columns"]["date"] == "ship_date"
    assert client.get(f"{url}?date_column=product", headers=owner.headers).status_code == 422

    run = client.post(
        f"{API}/datasets/{dataset['id']}/analyze",
        headers=owner.headers,
        json={"date_column": "ship_date"},
    ).json()
    assert run["config"]["date_column"] == "ship_date"
    insights = client.get(f"{API}/analysis-runs/{run['id']}/insights", headers=owner.headers).json()
    season = full(client, owner, of_type(insights, "seasonality")[0])
    assert "ship_date" in season["evidence"]["queries"][0]["sql"]
    bad = client.post(
        f"{API}/datasets/{dataset['id']}/analyze",
        headers=owner.headers,
        json={"date_column": "nope"},
    )
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_date_column"


class _Boom(AnalysisModule):
    name = "boom"
    title = "Exploding module"
    requires = frozenset[str]()

    def run(self, context: AnalysisContext) -> list[Insight]:
        raise RuntimeError("kaboom")


def test_a_failing_module_is_a_warning_and_the_others_continue(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, facts = make(tmp_path)
    registry.register(_Boom)
    try:
        _, run, insights = analyze(client, owner, content)
    finally:
        registry.unregister(_Boom)

    assert run["status"] == "completed_with_warnings"
    boom = run["progress"]["modules"]["boom"]
    assert boom["status"] == "failed" and "kaboom" in boom["error"]
    assert "kaboom" in boom["warnings"][0]
    assert run["progress"]["modules"]["kpis"]["status"] == "completed"
    kpi = full(client, owner, of_type(insights, "kpi_summary")[0])
    assert round(kpi["payload"]["net_revenue"] * 100) == facts["net_revenue_cents"]


@pytest.mark.slow
def test_rerun_replaces_previous_insights(client: TestClient, db: Session, tmp_path: Path) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    dataset_id, first, first_insights = analyze(client, owner, content)
    count = len(first_insights)
    assert count > 5

    second = client.post(
        f"{API}/datasets/{dataset_id}/analyze", headers=owner.headers, json={"currency": "USD"}
    ).json()
    assert second["id"] != first["id"] and second["status"] == "completed"
    again = client.get(f"{API}/analysis-runs/{second['id']}/insights", headers=owner.headers).json()
    assert len(again) == count
    assert "USD" in of_type(again, "kpi_summary")[0]["title"]
    old = client.get(f"{API}/analysis-runs/{first['id']}/insights", headers=owner.headers).json()
    assert old == []
    total = db.scalar(select(func.count()).select_from(InsightRow))
    assert total == count
    # settings of the earlier run are inherited when the re-run does not repeat them
    third = client.post(f"{API}/datasets/{dataset_id}/analyze", headers=owner.headers).json()
    assert third["config"]["currency"] == "USD"


def test_second_run_is_refused_while_one_is_active(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    dataset_id, run, _ = analyze(client, owner, content)
    stored = db.get(AnalysisRun, uuid.UUID(run["id"]))
    assert stored is not None
    stored.status = AnalysisStatus.running.value
    db.commit()
    response = client.post(f"{API}/datasets/{dataset_id}/analyze", headers=owner.headers)
    assert response.status_code == 409 and response.json()["code"] == "analysis_running"


def test_analysis_chains_after_profiling_when_enabled(
    client: TestClient, db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(client)
    content, facts = make(tmp_path)

    upload_ready(client, owner, content, "off.csv")
    assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 0  # off by default

    monkeypatch.setattr(get_settings(), "auto_analyze", True)
    dataset = upload_ready(client, owner, content, "auto.csv")
    run = db.scalars(select(AnalysisRun).order_by(AnalysisRun.created_at.desc())).first()
    assert run is not None and run.config["auto"] is True
    assert str(run.dataset_version_id) == dataset["versions"][0]["id"]
    assert run.status == AnalysisStatus.completed.value
    insights = client.get(f"{API}/analysis-runs/{run.id}/insights", headers=owner.headers).json()
    kpi = full(client, owner, of_type(insights, "kpi_summary")[0])
    assert round(kpi["payload"]["net_revenue"] * 100) == facts["net_revenue_cents"]


@pytest.mark.slow
def test_analysis_can_target_a_version(client: TestClient, db: Session, tmp_path: Path) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    dataset = upload_ready(client, owner, content, "v1.csv")
    added = upload(
        client,
        owner,
        drop_columns(content, {"customer_id"}),
        path=f"/datasets/{dataset['id']}/versions",
    )
    assert added.status_code == 201, added.text
    v1 = client.post(
        f"{API}/datasets/{dataset['id']}/analyze?version=1", headers=owner.headers
    ).json()
    current = client.post(f"{API}/datasets/{dataset['id']}/analyze", headers=owner.headers).json()
    assert v1["dataset_version_id"] != current["dataset_version_id"]
    assert v1["status"] == "completed" and current["status"] == "completed_with_warnings"
    both = db.scalars(select(InsightRow.dataset_version_id).distinct()).all()
    assert len(both) == 2  # a run replaces only its own version's insights
    assert (
        client.post(
            f"{API}/datasets/{dataset['id']}/analyze?version=9", headers=owner.headers
        ).status_code
        == 404
    )


def _stale_run(db: Session, version: DatasetVersion, status: str, age_minutes: int) -> AnalysisRun:
    run = AnalysisRun(
        tenant_id=version.tenant_id,
        dataset_version_id=version.id,
        status=status,
        progress={},
        config={},
        started_at=FROZEN_NOW - timedelta(minutes=age_minutes),
    )
    db.add(run)
    db.commit()
    return run


def test_stuck_analysis_runs_are_failed_by_the_sweeper(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    dataset = upload_ready(client, owner, content, "stuck.csv")
    version = db.get(DatasetVersion, uuid.UUID(dataset["versions"][0]["id"]))
    assert version is not None
    stuck = _stale_run(db, version, "running", 31)
    recent = _stale_run(db, version, "running", 5)
    done = _stale_run(db, version, "completed", 500)

    assert fail_stuck_ingestions(db, now=FROZEN_NOW) == 1

    db.refresh(stuck), db.refresh(recent), db.refresh(done)
    assert stuck.status == "failed" and stuck.error == ANALYSIS_STUCK_ERROR
    assert stuck.finished_at is not None
    assert recent.status == "running" and done.status == "completed"
    shown = client.get(f"{API}/analysis-runs/{stuck.id}", headers=owner.headers).json()
    assert shown["status"] == "failed"
    assert version.status == "ready"


def _version(db: Session, dataset: dict[str, Any]) -> DatasetVersion:
    version = db.get(DatasetVersion, uuid.UUID(dataset["versions"][0]["id"]))
    assert version is not None
    return version


def test_failure_paths_of_the_worker_and_dispatcher(
    client: TestClient, db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import analysis as service
    from app.workers.tasks import run_analysis_task

    owner = register(client)
    tenant = uuid.UUID(owner.tenant_id)
    content, _ = make(tmp_path)
    dataset = upload_ready(client, owner, content, "paths.csv")
    version = _version(db, dataset)

    # the queue is unreachable: the run is marked failed with a friendly message
    def unreachable(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("broker down")

    with monkeypatch.context() as patch:
        patch.setattr(run_analysis_task, "delay", unreachable)
        response = client.post(f"{API}/datasets/{dataset['id']}/analyze", headers=owner.headers)
    assert response.status_code == 202
    shown = client.get(f"{API}/analysis-runs/{response.json()['id']}", headers=owner.headers).json()
    assert shown["status"] == "failed" and shown["error"] == service.ANALYSIS_START_FAILED

    # an unreadable file fails the run, not the worker
    run = AnalysisRun(
        tenant_id=tenant, dataset_version_id=version.id, status="pending", config={}, progress={}
    )
    db.add(run)
    db.commit()
    with monkeypatch.context() as patch:
        patch.setattr(service, "open_analysis", unreachable)
        service.run_analysis(tenant, run.id)
    db.refresh(run)
    assert run.status == "failed" and run.error == service.ANALYSIS_FAILED_MESSAGE

    # runs that are missing or not pending are ignored
    service.run_analysis(tenant, run.id)
    service.run_analysis(tenant, uuid.uuid4())

    # when every module fails the run fails
    run = AnalysisRun(
        tenant_id=tenant, dataset_version_id=version.id, status="pending", config={}, progress={}
    )
    db.add(run)
    db.commit()
    with monkeypatch.context() as patch:
        patch.setattr(registry, "all_modules", lambda: [_Boom()])
        service.run_analysis(tenant, run.id)
    db.refresh(run)
    assert run.status == "failed" and run.progress["modules"]["boom"]["status"] == "failed"


def test_auto_enqueue_never_raises_and_skips_active_runs(
    client: TestClient, db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import analysis as service

    owner = register(client)
    tenant = uuid.UUID(owner.tenant_id)
    content, _ = make(tmp_path)
    version = _version(db, upload_ready(client, owner, content, "auto.csv"))

    _stale_run(db, version, "running", 1)
    service.enqueue_analysis(db, tenant, version)
    assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 1  # skipped

    db.query(AnalysisRun).delete()
    db.commit()

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "_merged_config", broken)
    service.enqueue_analysis(db, tenant, version)  # swallowed
    assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 0


@pytest.mark.slow
def test_list_analysis_runs_newest_first_with_filters(
    client: TestClient, db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    url_for = lambda ds: f"{API}/datasets/{ds}/analysis-runs"  # noqa: E731

    empty = upload_ready(client, owner, content, "empty.csv")
    assert client.get(url_for(empty["id"]), headers=owner.headers).json() == []

    monkeypatch.setattr(get_settings(), "auto_analyze", True)
    dataset = upload_ready(client, owner, content, "runs.csv")  # starts the automatic run
    added = upload(client, owner, content, path=f"/datasets/{dataset['id']}/versions")
    assert added.status_code == 201
    manual = client.post(f"{API}/datasets/{dataset['id']}/analyze?version=1", headers=owner.headers)
    assert manual.status_code == 202

    runs = client.get(url_for(dataset["id"]), headers=owner.headers).json()
    assert len(runs) == 3
    assert runs[0]["id"] == manual.json()["id"] and runs[0]["version"] == 1
    assert [r["version"] for r in runs] == [1, 2, 1]  # manual, auto on v2, auto on v1
    assert runs[2]["id"] != runs[0]["id"]
    assert all(r["status"] == "completed" for r in runs)
    expected = {"id", "dataset_version_id", "version", "status", "progress", "started_at"}
    assert expected | {"finished_at", "error", "insight_count"} == set(runs[0])
    # the newest run on a version owns that version's insights; replaced runs show none
    assert runs[0]["insight_count"] > 5 and runs[2]["insight_count"] == 0
    assert runs[1]["insight_count"] > 5

    only_v2 = client.get(f"{url_for(dataset['id'])}?version=2", headers=owner.headers).json()
    assert [r["version"] for r in only_v2] == [2]
    assert client.get(f"{url_for(dataset['id'])}?version=9", headers=owner.headers).json() == []
    assert len(client.get(f"{url_for(dataset['id'])}?limit=2", headers=owner.headers).json()) == 2
    for bad in ("limit=0", "limit=101", "version=0"):
        assert (
            client.get(f"{url_for(dataset['id'])}?{bad}", headers=owner.headers).status_code == 422
        )
    assert len(client.get(f"{url_for(dataset['id'])}?limit=100", headers=owner.headers).json()) == 3
    assert client.get(url_for(uuid.uuid4()), headers=owner.headers).status_code == 404


def test_other_tenants_cannot_list_runs(client: TestClient, tmp_path: Path) -> None:
    owner_a = register(client, company="A Corp")
    owner_b = register(client, company="B Corp")
    member_a = add_member(client, owner_a, "member")
    content, _ = make(tmp_path)
    dataset_id, _, _ = analyze(client, owner_a, content)
    url = f"{API}/datasets/{dataset_id}/analysis-runs"
    assert client.get(url, headers=owner_b.headers).status_code == 404
    assert len(client.get(url, headers=member_a.headers).json()) == 1  # any member may read
