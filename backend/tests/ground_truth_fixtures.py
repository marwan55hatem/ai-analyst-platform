"""Ground-truth retail data generated into a temp folder at test time, plus API helpers."""

import importlib.util
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi.testclient import TestClient

from tests.dataset_helpers import upload_ready
from tests.helpers import API, Account

GENERATOR = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "datasets"
    / "generators"
    / "ground_truth_generator.py"
)


@cache
def generator() -> ModuleType:
    """Import the ground-truth generator from evaluation/."""
    spec = importlib.util.spec_from_file_location("ground_truth_generator", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make(tmp_path: Path, lang: str = "en", refunds: bool = False) -> tuple[bytes, dict[str, Any]]:
    """Generate the dataset into tmp_path and return (csv bytes, planted facts)."""
    csv_path, _, facts = generator().generate(tmp_path, lang=lang, refunds=refunds)
    return csv_path.read_bytes(), facts


def analyze(
    client: TestClient, account: Account, content: bytes, body: dict[str, Any] | None = None
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Upload CSV bytes, run the analysis and return (dataset id, run, insight summaries)."""
    dataset = upload_ready(client, account, content, "truth.csv")
    response = client.post(
        f"{API}/datasets/{dataset['id']}/analyze", headers=account.headers, json=body or {}
    )
    assert response.status_code == 202, response.text
    run = client.get(f"{API}/analysis-runs/{response.json()['id']}", headers=account.headers).json()
    insights = client.get(
        f"{API}/analysis-runs/{run['id']}/insights?limit=200", headers=account.headers
    ).json()
    return dataset["id"], run, insights


def full(client: TestClient, account: Account, insight: dict[str, Any]) -> dict[str, Any]:
    """Fetch one insight with payload and evidence."""
    response = client.get(f"{API}/insights/{insight['id']}", headers=account.headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def of_type(insights: list[dict[str, Any]], kind: str, **where: Any) -> list[dict[str, Any]]:
    """Return the insights of one type."""
    return [i for i in insights if i["type"] == kind]
