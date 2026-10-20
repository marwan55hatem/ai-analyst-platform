"""Customer ids and PII-flagged columns never leak into insights, charts, evidence or other tenants."""

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.dataset_helpers import upload_ready
from tests.ground_truth_fixtures import full, generator
from tests.helpers import API, Account, register

CUSTOMER_ID = re.compile(r"\bC\d{4}\b")


def _with_contacts(tmp_path: Path) -> tuple[bytes, list[str]]:
    """The grouped dataset with emails as customer ids plus email and phone columns."""
    csv_path, _, _ = generator().generate(tmp_path, segments=True, anomalies=True)
    rows = list(csv.reader(io.StringIO(csv_path.read_text(encoding="utf-8"))))
    header, body = rows[0], rows[1:]
    cust = header.index("customer_id")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([*header, "contact_email", "phone"])
    secrets: set[str] = set()
    for r in body:
        number = int(r[cust][1:])
        email = f"buyer{number:04d}@example.com"
        phone = f"+1 555 01{number:04d}"
        r[cust] = email
        secrets.update({email, phone, f"buyer{number:04d}"})
        writer.writerow([*r, email, phone])
    return out.getvalue().encode(), sorted(secrets)


def _run(client: TestClient, owner: Account, content: bytes) -> tuple[str, dict[str, Any]]:
    dataset = upload_ready(client, owner, content, "contacts.csv")
    profile = client.get(f"{API}/datasets/{dataset['id']}/profile", headers=owner.headers).json()
    flagged = {c["name"]: c for c in profile["columns"] if c["pii_flag"]}
    assert {"customer_id", "contact_email", "phone"} <= set(flagged)
    response = client.put(
        f"{API}/datasets/{dataset['id']}/columns/customer_id/role",
        headers=owner.headers,
        json={"role": "customer_id"},
    )
    assert response.status_code == 200, response.text
    started = client.post(f"{API}/datasets/{dataset['id']}/analyze", headers=owner.headers, json={})
    run = client.get(f"{API}/analysis-runs/{started.json()['id']}", headers=owner.headers).json()
    return dataset["id"], run


def _leaks(blob: Any, secrets: list[str]) -> list[str]:
    dumped = json.dumps(blob, default=str)
    return [s for s in secrets if s in dumped]


def test_pii_never_appears_in_insights_and_assignments_are_pseudonyms(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    owner = register(client)
    content, secrets = _with_contacts(tmp_path)
    _, run = _run(client, owner, content)
    assert run["progress"]["modules"]["segmentation"]["status"] == "completed", run["progress"]
    insights = client.get(
        f"{API}/analysis-runs/{run['id']}/insights?limit=200", headers=owner.headers
    ).json()
    assert {i["module"] for i in insights} >= {"anomalies", "segmentation"}
    for summary in insights:
        item = full(client, owner, summary)
        shown = {
            "title": item["title"],
            "summary": item["summary"],
            "chart": item["chart_spec"],
            "evidence": [q["rows"] for q in item["evidence"]["queries"]],
            "payload": item["payload"],
        }
        assert _leaks(shown, secrets) == [], (item["module"], item["type"])
    anomalies = client.get(f"{API}/analysis-runs/{run['id']}/anomalies", headers=owner.headers)
    assert _leaks(anomalies.json(), secrets) == []
    segments = client.get(
        f"{API}/analysis-runs/{run['id']}/segments?limit=500", headers=owner.headers
    ).json()
    assert segments["pseudonymized_customer_ids"] is True
    assert _leaks(segments, secrets) == []
    keys = [a["customer_key"] for a in segments["assignments"]["items"]]
    assert keys and all(k.startswith("cust_") and len(k) == 21 for k in keys)
    stored = db.execute(text("SELECT customer_key FROM segment_assignments")).scalars().all()
    assert stored and not _leaks(list(stored), secrets)
    clusters = next(i for i in segments["insights"] if i["type"] == "customer_clusters")
    assert "pseudonyms" in clusters["summary"]


def test_customer_ids_stay_out_of_insights_and_assignments_stay_in_the_tenant(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    owner_a = register(client, company="A Corp")
    owner_b = register(client, company="B Corp")
    csv_path, _, _ = generator().generate(tmp_path, segments=True)
    dataset = upload_ready(client, owner_a, csv_path.read_bytes(), "grouped.csv")
    started = client.post(
        f"{API}/datasets/{dataset['id']}/analyze", headers=owner_a.headers, json={}
    )
    run_id = started.json()["id"]
    insights = client.get(
        f"{API}/analysis-runs/{run_id}/insights?limit=200&module=segmentation",
        headers=owner_a.headers,
    ).json()
    for summary in insights:
        item = full(client, owner_a, summary)
        text_parts = json.dumps(
            [item["title"], item["summary"], item["chart_spec"], item["evidence"], item["payload"]]
        )
        assert not CUSTOMER_ID.search(text_parts), item["type"]
    page = client.get(f"{API}/analysis-runs/{run_id}/segments", headers=owner_a.headers).json()
    assert CUSTOMER_ID.fullmatch(page["assignments"]["items"][0]["customer_key"])
    other = client.get(f"{API}/analysis-runs/{run_id}/segments", headers=owner_b.headers)
    assert other.status_code == 404
    assert other.json()["code"] == "not_found"
    tenants = db.execute(text("SELECT DISTINCT tenant_id FROM segment_assignments")).scalars().all()
    assert [str(t) for t in tenants] == [owner_a.tenant_id]
