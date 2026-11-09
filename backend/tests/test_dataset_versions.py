"""New dataset versions: compatibility check, per-upload analysis flag, history, alerts rule."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.services import alerts
from tests.dataset_helpers import csv_bytes, upload, upload_ready
from tests.helpers import API, Account, register

HEADER = ["order_date", "revenue", "region"]


def _rows(n: int = 40) -> list[list[object]]:
    return [
        [f"2024-{1 + (i % 12):02d}-{1 + (i % 27):02d}", (i + 1) * 10, "north" if i % 2 else "south"]
        for i in range(n)
    ]


def _january(n: int) -> list[list[object]]:
    return [[f"2024-01-{1 + i % 27:02d}", 10 + i, "n"] for i in range(n)]


def _base(client: TestClient, owner: Account, rows: list[list[object]] | None = None) -> str:
    made = upload_ready(client, owner, csv_bytes(rows or _rows(), HEADER), "v1.csv")
    return str(made["id"])


def _add(
    client: TestClient, owner: Account, dataset: str, content: bytes, **fields: Any
) -> dict[str, Any]:
    response = upload(
        client, owner, content, "v2.csv", path=f"/datasets/{dataset}/versions", **fields
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _compat(client: TestClient, owner: Account, dataset: str, version: int) -> dict[str, Any]:
    response = client.get(
        f"{API}/datasets/{dataset}/versions/{version}/compatibility", headers=owner.headers
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_first_version_has_no_previous(client: TestClient) -> None:
    owner = register(client)
    dataset = _base(client, owner)
    body = _compat(client, owner, dataset, 1)
    assert body["status"] == "initial" and body["previous_version"] is None


def test_same_columns_are_compatible(client: TestClient) -> None:
    owner = register(client)
    dataset = _base(client, owner)
    _add(client, owner, dataset, csv_bytes(_rows(), HEADER))
    body = _compat(client, owner, dataset, 2)
    assert body["status"] == "compatible" and body["previous_version"] == 1
    assert body["added"] == body["missing"] == body["changed"] == []
    assert not body["shorter_period"] and not body["blocking"]


def test_added_columns_never_block_or_warn(client: TestClient) -> None:
    owner = register(client)
    dataset = _base(client, owner)
    rows = [[*r, "web"] for r in _rows()]
    _add(client, owner, dataset, csv_bytes(rows, [*HEADER, "channel"]))
    body = _compat(client, owner, dataset, 2)
    assert body["added"] == ["channel"] and body["status"] == "compatible"


def test_missing_revenue_role_warns_but_does_not_block(client: TestClient) -> None:
    owner = register(client)
    dataset = _base(client, owner)
    rows = [[r[0], r[2], i + 1] for i, r in enumerate(_rows())]
    _add(client, owner, dataset, csv_bytes(rows, ["order_date", "region", "quantity"]))
    body = _compat(client, owner, dataset, 2)
    assert body["missing"] == ["revenue"] and "revenue" in body["missing_roles"]
    assert body["status"] == "warnings" and not body["blocking"]  # date + quantity can be analysed


def test_changed_type_is_reported(client: TestClient) -> None:
    owner = register(client)
    dataset = _base(client, owner)
    rows = [[r[0], r[1], 1000 + i] for i, r in enumerate(_rows())]
    _add(client, owner, dataset, csv_bytes(rows, HEADER))
    body = _compat(client, owner, dataset, 2)
    region = next(c for c in body["changed"] if c["name"] == "region")
    assert region["previous_type"] != region["new_type"]
    assert body["status"] == "warnings"


def test_file_without_date_or_amount_is_blocked(client: TestClient) -> None:
    owner = register(client)
    dataset = _base(client, owner)
    rows = [[f"c{i}", "x"] for i in range(30)]
    _add(client, owner, dataset, csv_bytes(rows, ["note", "label"]))
    body = _compat(client, owner, dataset, 2)
    assert body["status"] == "blocked" and body["blocking"] and body["blocking_reason"]


def test_unreadable_file_is_blocked(client: TestClient) -> None:
    owner = register(client)
    dataset = _base(client, owner)
    response = upload(client, owner, b"x,y\n", "v2.csv", path=f"/datasets/{dataset}/versions")
    assert response.status_code == 201
    body = _compat(client, owner, dataset, 2)
    assert body["status"] == "blocked" and body["blocking_reason"]


def test_shorter_period_and_fewer_rows_warn_without_blocking(client: TestClient) -> None:
    owner = register(client)
    dataset = _base(client, owner, _january(40))
    later = [[f"2024-03-{1 + i % 27:02d}", 10 + i, "n"] for i in range(40)]
    _add(client, owner, dataset, csv_bytes(later, HEADER))
    body = _compat(client, owner, dataset, 2)
    assert body["shorter_period"] and body["status"] == "warnings" and not body["blocking"]
    assert body["new_first_date"] > body["previous_first_date"]

    _add(client, owner, dataset, csv_bytes(_january(10), HEADER))
    body = _compat(client, owner, dataset, 3)
    assert body["shorter_period"] and body["new_rows"] == 10 and body["previous_rows"] == 40


def test_same_period_and_enough_rows_do_not_warn(client: TestClient) -> None:
    owner = register(client)
    dataset = _base(client, owner, _january(40))
    _add(client, owner, dataset, csv_bytes(_january(25), HEADER))  # 62% of the rows, same start
    assert not _compat(client, owner, dataset, 2)["shorter_period"]


def test_compatibility_validation_and_unknown_version(client: TestClient) -> None:
    owner = register(client)
    dataset = _base(client, owner)
    url = f"{API}/datasets/{dataset}/versions"
    assert client.get(f"{url}/9/compatibility", headers=owner.headers).status_code == 404
    assert client.get(f"{url}/abc/compatibility", headers=owner.headers).status_code == 422
    missing = client.get(f"{API}/datasets/{uuid.uuid4()}/versions", headers=owner.headers)
    assert missing.status_code == 404
    bad = upload(client, owner, b"nope", "notes.txt", path=f"/datasets/{dataset}/versions")
    assert bad.status_code in (415, 422)


def test_analyze_flag_controls_the_automatic_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "auto_analyze", True)
    owner = register(client)
    dataset = _base(client, owner)
    runs = f"{API}/datasets/{dataset}/analysis-runs"
    assert [r["version"] for r in client.get(runs, headers=owner.headers).json()] == [1]

    _add(client, owner, dataset, csv_bytes(_rows(), HEADER), analyze="false")
    assert [r["version"] for r in client.get(runs, headers=owner.headers).json()] == [1]

    _add(client, owner, dataset, csv_bytes(_rows(), HEADER))
    assert sorted(r["version"] for r in client.get(runs, headers=owner.headers).json()) == [1, 3]
    versions = client.get(f"{API}/datasets/{dataset}/versions", headers=owner.headers).json()
    assert {v["version"]: v["auto_analyze"] for v in versions} == {1: True, 2: False, 3: True}


def test_blocked_file_is_not_analysed_automatically(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "auto_analyze", True)
    owner = register(client)
    dataset = _base(client, owner)
    _add(client, owner, dataset, csv_bytes([[f"c{i}", "x"] for i in range(30)], ["note", "label"]))
    runs = client.get(f"{API}/datasets/{dataset}/analysis-runs", headers=owner.headers).json()
    assert [r["version"] for r in runs] == [1]


def test_versions_list_has_source_rows_quality_and_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "auto_analyze", True)
    owner = register(client)
    dataset = _base(client, owner)
    _add(client, owner, dataset, csv_bytes(_rows(30), HEADER))
    versions = client.get(f"{API}/datasets/{dataset}/versions", headers=owner.headers).json()
    assert [v["version"] for v in versions] == [2, 1]
    newest = versions[0]
    assert newest["source"] == "upload" and newest["row_count"] == 30
    assert newest["is_current"] and newest["quality_score"] is not None
    assert newest["analysis_status"].startswith("completed") and newest["alerts_raised"] == 0
    assert not any("path" in key for key in newest)


def test_other_tenant_gets_404_for_the_new_routes(client: TestClient) -> None:
    owner = register(client)
    other = register(client)
    dataset = _base(client, owner)
    for path in (f"/datasets/{dataset}/versions", f"/datasets/{dataset}/versions/1/compatibility"):
        assert client.get(f"{API}{path}", headers=other.headers).status_code == 404
    assert (
        client.get(f"{API}/datasets/{dataset}/versions", headers=owner.headers).status_code == 200
    )


def test_cleaning_derived_versions_raise_no_alerts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run on a cleaned copy keeps its summary but is never compared for alerts."""
    owner = register(client)
    dataset = _base(client, owner, _january(30) + _january(30)[:10])  # 10 exact duplicates
    first = client.post(f"{API}/datasets/{dataset}/analyze", headers=owner.headers).json()
    assert first["status"].startswith("completed")
    draft = alerts.Draft(
        rule="revenue_drop", severity="warning", title="t", body="b", dedupe_key="k", payload={}
    )
    monkeypatch.setattr(alerts, "compare", lambda *a, **k: [draft])

    _add(client, owner, dataset, csv_bytes(_january(30), HEADER))  # an upload is compared
    client.post(f"{API}/datasets/{dataset}/analyze", headers=owner.headers)
    assert client.get(f"{API}/alerts", headers=owner.headers).json()["total"] == 1

    plan = client.get(
        f"{API}/datasets/{dataset}/cleaning-plan?version=1", headers=owner.headers
    ).json()
    dedupe = [f["id"] for f in plan["fixes"] if "dup" in f["id"]]
    assert dedupe, plan
    applied = client.post(
        f"{API}/datasets/{dataset}/cleaning-plan/apply?version=1",
        headers=owner.headers,
        json={"fixes": [{"id": dedupe[0]}]},
    )
    assert applied.status_code in (200, 201, 202), applied.text
    versions = client.get(f"{API}/datasets/{dataset}/versions", headers=owner.headers).json()
    cleaned = versions[0]
    assert cleaned["source"] == "cleaning" and cleaned["row_count"] < 40

    other = alerts.Draft(
        rule="orders_drop", severity="warning", title="t2", body="b", dedupe_key="k2", payload={}
    )
    monkeypatch.setattr(alerts, "compare", lambda *a, **k: [other])
    client.post(
        f"{API}/datasets/{dataset}/analyze?version={cleaned['version']}", headers=owner.headers
    )
    assert client.get(f"{API}/alerts", headers=owner.headers).json()["total"] == 1
    last = client.get(f"{API}/datasets/{dataset}/versions", headers=owner.headers).json()[0]
    assert last["alerts_raised"] == 0


def test_preview_can_show_an_older_version(client: TestClient) -> None:
    owner = register(client)
    dataset = _base(client, owner)
    _add(client, owner, dataset, csv_bytes(_rows(30), HEADER))
    url = f"{API}/datasets/{dataset}/preview"
    assert client.get(url, headers=owner.headers).json()["total_rows"] == 30
    older = client.get(f"{url}?version=1", headers=owner.headers)
    assert older.status_code == 200 and older.json()["total_rows"] == 40
    assert client.get(f"{url}?version=9", headers=owner.headers).status_code == 404
    other = register(client)
    assert client.get(f"{url}?version=1", headers=other.headers).status_code == 404
