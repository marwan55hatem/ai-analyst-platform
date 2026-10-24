"""Scripted analyst end to end on an analysed planted dataset: real tools, traced numbers."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.agent_helpers import ask, conversation, kinds
from tests.dataset_helpers import upload_ready
from tests.ground_truth_fixtures import generator
from tests.helpers import API, register

pytestmark = pytest.mark.slow

QUESTIONS = {
    "What is our net revenue?": ["get_insights"],
    "Forecast revenue for the next months": ["get_forecast"],
    "Were there any anomalies?": ["get_anomalies"],
    "Show me the customer segments": ["get_segments"],
    "Which customers are at churn risk?": ["get_churn_risk"],
    "What if I raise Office prices 10%?": ["what_if"],
    "What should I do to grow profit?": ["get_insights", "explain_drivers", "what_if"],
}


def test_scripted_analyst_answers_with_traced_numbers(client: TestClient, tmp_path: Path) -> None:
    csv_path, _, _ = generator().generate(
        tmp_path, years=2, churn=True, elasticity=True, drivers=True, anomalies=True
    )
    owner = register(client)
    dataset = upload_ready(client, owner, csv_path.read_bytes(), "planted.csv")
    run = client.post(f"{API}/datasets/{dataset['id']}/analyze", headers=owner.headers, json={})
    assert run.status_code == 202, run.text
    conversation_id = conversation(client, owner, dataset["id"])

    for question, tools in QUESTIONS.items():
        events = ask(client, owner, conversation_id, question)
        order = kinds(events)
        assert order[0] == "tool_call" and order[-1] == "final", (question, order)
        called = [e.data["name"] for e in events if e.type == "tool_call"]
        assert called == tools, question
        results = [e.data for e in events if e.type == "tool_result"]
        assert all(r["ok"] for r in results), (question, [r["summary"] for r in results])
        final = events[-1].data
        assert final["scripted"] is True and final["untraced_numbers"] == [], (
            question,
            final["text"],
        )
        for heading in ("**Recommendation**", "**Why (evidence)**", "**Expected impact (range)**"):
            assert heading in final["text"], question

    messages = client.get(
        f"{API}/conversations/{conversation_id}/messages?limit=500", headers=owner.headers
    ).json()
    assert sum(m["role"] == "user" for m in messages) == len(QUESTIONS)
    assert all(m["evidence"] for m in messages if m["role"] == "tool")
