"""Churn, drivers and what-if through the API: pages, re-runs, replay, limits, skips and privacy."""

import csv
import io
import json
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import whatif
from app.services.query import SafeQueryService, validate_sql
from tests.dataset_helpers import upload_ready
from tests.evidence_helpers import replay_evidence
from tests.ground_truth_fixtures import analyze, full
from tests.helpers import API, Account, register
from tests.ml_context import generate

pytestmark = pytest.mark.slow


def _content(tmp_path: Path, **options: Any) -> bytes:
    csv_path, _ = generate(tmp_path, **options)
    return csv_path.read_bytes()


def _module(insights: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [i for i in insights if i["module"] == name]


def test_churn_and_drivers_endpoints_pages_rerun_and_replay(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    owner = register(client)
    content = _content(tmp_path, churn=True, elasticity=True, drivers=True)
    dataset_id, run, insights = analyze(client, owner, content)
    modules = run["progress"]["modules"]
    assert modules["churn"]["status"] == "completed", modules["churn"]
    assert modules["drivers"]["status"] == "completed", modules["drivers"]

    page = client.get(f"{API}/analysis-runs/{run['id']}/churn?limit=20", headers=owner.headers)
    assert page.status_code == 200, page.text
    body = page.json()
    items, total = body["customers"]["items"], body["customers"]["total"]
    assert len(items) == 20 and total > 500
    assert [i["probability"] for i in items] == sorted(
        (i["probability"] for i in items), reverse=True
    )
    assert all(len(i["reasons"]) == 3 for i in items)
    assert body["basis"] == "probability" and body["model"]["validation"] == "out_of_time"
    assert body["model"]["models"][body["model"]["winner"]]["auc"] >= 0.75
    second = client.get(
        f"{API}/analysis-runs/{run['id']}/churn?limit=20&offset=20", headers=owner.headers
    ).json()["customers"]["items"]
    assert not {i["customer_key"] for i in items} & {i["customer_key"] for i in second}
    high = client.get(
        f"{API}/analysis-runs/{run['id']}/churn?risk_band=high&limit=500", headers=owner.headers
    ).json()
    assert {i["risk_band"] for i in high["customers"]["items"]} <= {"high"}
    assert high["customers"]["total"] == body["bands"]["high"]["customers"]
    top = client.get(
        f"{API}/analysis-runs/{run['id']}/churn?rank_band=top_10pct", headers=owner.headers
    ).json()
    assert top["customers"]["total"] == round(0.1 * total)
    bad = client.get(
        f"{API}/analysis-runs/{run['id']}/churn?risk_band=extreme", headers=owner.headers
    )
    assert bad.status_code == 422

    drivers = client.get(f"{API}/analysis-runs/{run['id']}/drivers", headers=owner.headers).json()
    assert drivers["association_not_causation"] is True
    assert "discount" in [d["factor"] for d in drivers["importance"][:2]]
    assert set(drivers["partial_dependence"]) == {d["factor"] for d in drivers["importance"][:3]}
    driver_insight = drivers["insights"][0]
    assert driver_insight["confidence"] in ("medium", "low")
    assert "not proven cause" in driver_insight["summary"]

    checked = replay_evidence(
        client, db, owner, run, _module(insights, "churn") + _module(insights, "drivers")
    )
    assert checked >= 6

    # A re-run replaces the version's scores: the old run lists none, the table holds one set.
    again = client.post(f"{API}/datasets/{dataset_id}/analyze", headers=owner.headers, json={})
    assert again.status_code == 202
    new_total = client.get(
        f"{API}/analysis-runs/{again.json()['id']}/churn", headers=owner.headers
    ).json()["customers"]["total"]
    old_total = client.get(f"{API}/analysis-runs/{run['id']}/churn", headers=owner.headers).json()[
        "customers"
    ]["total"]
    stored = db.execute(text("SELECT count(*) FROM churn_scores")).scalar_one()
    assert new_total == total and old_total == 0 and stored == total


def _what_if(
    client: TestClient, owner: Account, dataset_id: str, *scenarios: dict[str, Any]
) -> Any:
    return client.post(
        f"{API}/datasets/{dataset_id}/what-if",
        headers=owner.headers,
        json={"scenarios": list(scenarios)},
    )


def test_what_if_ranges_comparison_warnings_and_limits(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    owner = register(client)
    content = _content(tmp_path, elasticity=True, drivers=True)
    dataset = upload_ready(client, owner, content, "whatif.csv")
    office = {"lever": "price", "change_pct": 10, "scope": "category", "target": "Office"}

    single = _what_if(client, owner, dataset["id"], office)
    assert single.status_code == 200, single.text
    result = single.json()["scenarios"][0]
    assert result["status"] == "ok" and not result["extrapolation_risk"]
    assert result["revenue_change"]["low"] < result["revenue_change"]["high"]
    assert result["profit_change"] is not None and result["assumptions"]
    assert abs(result["elasticity"]["elasticity"] + 1.5) <= 0.3
    assert "not a promise" in single.json()["note"]

    three = _what_if(
        client,
        owner,
        dataset["id"],
        office,
        {"lever": "discount", "change_pct": 20},
        {"lever": "volume", "change_pct": -10},
    )
    assert three.status_code == 200, three.text
    assert [s["lever"] for s in three.json()["scenarios"]] == ["price", "discount", "volume"]

    outside = _what_if(client, owner, dataset["id"], {**office, "change_pct": 28}).json()
    warned = outside["scenarios"][0]
    assert warned["extrapolation_risk"] is True
    assert any(w.startswith("Extrapolation risk") for w in warned["warnings"])

    too_big = _what_if(client, owner, dataset["id"], {**office, "change_pct": 35})
    assert too_big.status_code == 422 and too_big.json()["code"] == "validation_error"
    four = _what_if(client, owner, dataset["id"], office, office, office, office)
    assert four.status_code == 422
    unknown = _what_if(client, owner, dataset["id"], {**office, "target": "Nope"})
    assert unknown.status_code == 422 and unknown.json()["code"] == "unknown_target"
    lever = _what_if(client, owner, dataset["id"], {"lever": "magic", "change_pct": 5})
    assert lever.status_code == 422

    full_slots = threading.BoundedSemaphore(1)
    full_slots.acquire()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(whatif, "_slots", full_slots)
        patch.setattr(whatif, "WAIT_SECONDS", 0.01)
        busy = _what_if(client, owner, dataset["id"], office)
    assert busy.status_code == 409 and busy.json()["code"] == "what_if_busy"

    service = SafeQueryService(db)
    version_id = uuid.UUID(single.json()["dataset_version_id"])
    for query in result["evidence"]:
        validate_sql(query["sql"])
        again = service.run(uuid.UUID(owner.tenant_id), version_id, query["sql"], 5000)
        assert again.columns == query["columns"] and len(again.rows) == query["row_count"]


def test_too_few_customers_is_a_plain_capability_reason(client: TestClient, tmp_path: Path) -> None:
    owner = register(client)
    lines = _content(tmp_path).decode().splitlines()
    small = ("\n".join(lines[:1200]) + "\n").encode()  # the first months: a few hundred customers
    dataset_id, run, _ = analyze(client, owner, small)
    caps = client.get(f"{API}/datasets/{dataset_id}/capabilities", headers=owner.headers).json()
    churn = next(m for m in caps["modules"] if m["name"] == "churn")
    assert churn["available"] is False
    assert churn["reason"].startswith("only ")
    assert churn["reason"].endswith(
        " customers; churn risk needs at least 500, so churn risk is skipped"
    )
    body = client.get(f"{API}/analysis-runs/{run['id']}/churn", headers=owner.headers).json()
    assert body["module_status"] == "skipped" and body["skipped_reason"] == churn["reason"]
    assert body["customers"]["total"] == 0


def _with_contacts(tmp_path: Path) -> tuple[bytes, list[str]]:
    """Planted churn data whose customer ids are emails, plus email and phone columns."""
    csv_path, _ = generate(tmp_path, churn=True, drivers=True)
    rows = list(csv.reader(io.StringIO(csv_path.read_text(encoding="utf-8"))))
    header, body = rows[0], rows[1:]
    cust = header.index("customer_id")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([*header, "contact_email", "phone"])
    secrets: set[str] = set()
    for r in body:
        number = int(r[cust][1:])
        email, phone = f"buyer{number:04d}@example.com", f"+1 555 01{number:04d}"
        secrets.update({email, phone})
        r[cust] = email
        writer.writerow([*r, email, phone])
    return out.getvalue().encode(), sorted(secrets)


def test_personal_customer_ids_never_leave_as_raw_values(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, secrets = _with_contacts(tmp_path)
    dataset = upload_ready(client, owner, content, "contacts.csv")
    profile = client.get(f"{API}/datasets/{dataset['id']}/profile", headers=owner.headers).json()
    assert "customer_id" in {c["name"] for c in profile["columns"] if c["pii_flag"]}
    response = client.put(
        f"{API}/datasets/{dataset['id']}/columns/customer_id/role",
        headers=owner.headers,
        json={"role": "customer_id"},
    )
    assert response.status_code == 200, response.text
    started = client.post(f"{API}/datasets/{dataset['id']}/analyze", headers=owner.headers, json={})
    run = client.get(f"{API}/analysis-runs/{started.json()['id']}", headers=owner.headers).json()
    assert run["progress"]["modules"]["churn"]["status"] == "completed", run["progress"]
    churn = client.get(f"{API}/analysis-runs/{run['id']}/churn?limit=500", headers=owner.headers)
    body = churn.json()
    assert body["pseudonymized_customer_ids"] is True
    assert all(i["customer_key"].startswith("cust_") for i in body["customers"]["items"])
    insights = client.get(
        f"{API}/analysis-runs/{run['id']}/insights?limit=200", headers=owner.headers
    ).json()
    shown = [full(client, owner, i) for i in insights if i["module"] in ("churn", "drivers")]
    assert {i["module"] for i in shown} == {"churn", "drivers"}
    dumped = json.dumps([shown, body], default=str)
    assert [s for s in secrets if s in dumped] == []
    assert "@example.com" not in dumped
