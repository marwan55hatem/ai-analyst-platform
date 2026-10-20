"""The segmentation module on planted customer groups, inside the pipeline and through the API."""

import csv
import io
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sklearn.metrics import adjusted_rand_score

from tests.dataset_helpers import upload_ready
from tests.ground_truth_fixtures import analyze, full, generator, of_type
from tests.helpers import API, Account, register

PAGE = 500


def _grouped(tmp_path: Path, seed: int = 11) -> tuple[bytes, dict[str, Any]]:
    csv_path, _, facts = generator().generate(tmp_path, seed=seed, segments=True)
    return csv_path.read_bytes(), facts


def _all_assignments(client: TestClient, owner: Account, run_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    while True:
        page = client.get(
            f"{API}/analysis-runs/{run_id}/segments?limit={PAGE}&offset={len(items)}",
            headers=owner.headers,
        ).json()["assignments"]
        items += page["items"]
        if len(items) >= page["total"]:
            return items


def test_planted_customer_groups_are_recovered(client: TestClient, tmp_path: Path) -> None:
    owner = register(client)
    content, facts = _grouped(tmp_path)
    _, run, insights = analyze(client, owner, content)
    assert run["progress"]["modules"]["segmentation"]["status"] == "completed", run["progress"]
    assignments = _all_assignments(client, owner, run["id"])
    cluster = {a["customer_key"]: a["cluster"] for a in assignments}
    planted = facts["planted"]["segments"]
    truth = {c: name for name, members in planted.items() for c in members}
    keys = sorted(truth)
    ari = adjusted_rand_score([truth[k] for k in keys], [cluster[k] for k in keys])
    assert ari >= 0.6, ari
    groups = full(client, owner, of_type(insights, "customer_clusters")[0])
    stability = groups["payload"]["stability"]
    assert stability["rounds"] == 20 and stability["median_ari"] is not None
    assert groups["confidence"] in {"high", "medium"}
    assert all(
        c["recommended_action"]["kind"] == "suggestion"
        and c["recommended_action"]["text"].startswith("Suggestion: ")
        for c in groups["payload"]["clusters"]
    )
    names = [c["name"] for c in groups["payload"]["clusters"]]
    assert len(set(names)) == len(names)


@pytest.mark.slow
def test_segments_are_reproducible(client: TestClient, tmp_path: Path) -> None:
    owner = register(client)
    content, _ = _grouped(tmp_path)
    _, first, _ = analyze(client, owner, content)
    _, second, _ = analyze(client, owner, content)
    a = {
        x["customer_key"]: (x["cluster"], x["rfm_segment"])
        for x in _all_assignments(client, owner, first["id"])
    }
    b = {
        x["customer_key"]: (x["cluster"], x["rfm_segment"])
        for x in _all_assignments(client, owner, second["id"])
    }
    assert a == b and a


def test_rfm_segments_cover_every_customer_with_the_planted_lapsed_group_at_risk(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, facts = _grouped(tmp_path)
    _, run, insights = analyze(client, owner, content)
    body = client.get(f"{API}/analysis-runs/{run['id']}/segments", headers=owner.headers).json()
    assert body["module_status"] == "completed"
    assert body["pseudonymized_customer_ids"] is False
    total = sum(s["customers"] for s in body["segments"])
    assert total == body["assignments"]["total"] == facts["customer_count"]
    by_key = {a["customer_key"]: a for a in _all_assignments(client, owner, run["id"])}
    lapsed = [by_key[c]["rfm_segment"] for c in facts["planted"]["segments"]["lapsed"]]
    assert sum(s in {"At risk", "Hibernating", "Lost"} for s in lapsed) / len(lapsed) > 0.9
    vip = [by_key[c]["rfm_segment"] for c in facts["planted"]["segments"]["vip"]]
    assert sum(s in {"Champions", "Loyal"} for s in vip) / len(vip) > 0.9
    rfm = full(client, owner, of_type(insights, "rfm_segments")[0])
    assert all(s["recommended_action"]["kind"] == "suggestion" for s in rfm["payload"]["segments"])
    assert "do not explain why" in rfm["summary"]


def test_assignments_are_paginated_and_filtered(client: TestClient, tmp_path: Path) -> None:
    owner = register(client)
    content, _ = _grouped(tmp_path)
    _, run, _ = analyze(client, owner, content)
    url = f"{API}/analysis-runs/{run['id']}/segments"
    first = client.get(f"{url}?limit=10", headers=owner.headers).json()["assignments"]
    second = client.get(f"{url}?limit=10&offset=10", headers=owner.headers).json()["assignments"]
    assert len(first["items"]) == len(second["items"]) == 10
    assert {i["customer_key"] for i in first["items"]}.isdisjoint(
        {i["customer_key"] for i in second["items"]}
    )
    lost = client.get(f"{url}?segment=Lost&limit=500", headers=owner.headers).json()
    assert all(i["rfm_segment"] == "Lost" for i in lost["assignments"]["items"])
    one = client.get(f"{url}?cluster=0&limit=500", headers=owner.headers).json()
    assert all(i["cluster"] == 0 for i in one["assignments"]["items"])
    for bad in ("limit=0", "limit=501", "offset=-1", "segment=Unknown", "cluster=-1"):
        response = client.get(f"{url}?{bad}", headers=owner.headers)
        assert response.status_code == 422, bad


def _few_customers(customers: int) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["order_id", "order_date", "customer_id", "product", "revenue"])
    n = 0
    for day in range(1, 29):
        for month in range(1, 13):
            n += 1
            writer.writerow(
                [
                    f"O{n}",
                    f"2024-{month:02d}-{day:02d}",
                    f"C{n % customers:03d}",
                    "Item",
                    10 + n % 7,
                ]
            )
    return out.getvalue().encode()


def test_forty_customers_skip_segmentation_with_a_reason(client: TestClient) -> None:
    owner = register(client)
    dataset = upload_ready(client, owner, _few_customers(40), "small.csv")
    caps = client.get(f"{API}/datasets/{dataset['id']}/capabilities", headers=owner.headers)
    segmentation = next(m for m in caps.json()["modules"] if m["name"] == "segmentation")
    assert segmentation["available"] is False
    reason = "only 40 customers; customer segmentation needs at least 100, so customer segmentation is skipped"
    assert segmentation["reason"] == reason
    response = client.post(
        f"{API}/datasets/{dataset['id']}/analyze", headers=owner.headers, json={}
    )
    run = client.get(f"{API}/analysis-runs/{response.json()['id']}", headers=owner.headers).json()
    assert run["status"] == "completed_with_warnings"
    assert run["progress"]["modules"]["segmentation"] == {
        "status": "skipped",
        "reason": reason,
        "insights": 0,
        "warnings": [],
    }
    body = client.get(f"{API}/analysis-runs/{run['id']}/segments", headers=owner.headers).json()
    assert body["skipped_reason"] == reason
    assert body["assignments"]["total"] == 0 and body["segments"] == []
