"""A full chat turn over a mocked provider: real customer ids never cross the wire, people see them.

The provider is the real OpenAI-compatible client on an httpx.MockTransport that records every
request body. The scripted turn reads data with every tool that can return customers, and the
question contains a real customer id typed by the user.
"""

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent import service as chat_service
from app.core.config import get_settings
from tests.agent_helpers import FakeSandbox, ask, conversation
from tests.ground_truth_fixtures import analyze
from tests.helpers import API, register
from tests.ml_context import generate
from tests.test_agent_groq import real_groq
from tests.test_llm_client import delta, sse

pytestmark = pytest.mark.slow
CODE = re.compile(r"CUST-[0-9A-HJKMNP-TV-Z]{8,10}")


def call_chunk(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return delta(
        tool_calls=[
            {
                "index": 0,
                "id": f"call_{name}_{len(arguments)}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ]
    )


def customer_ids(content: bytes) -> list[str]:
    rows = csv.DictReader(io.StringIO(content.decode()))
    return sorted({r["customer_id"] for r in rows if r.get("customer_id")})


def test_no_real_customer_id_in_provider_requests(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeSandbox()
    fake.answer = {"ok": True, "result": 1, "stdout": "", "duration_ms": 3}
    monkeypatch.setattr(chat_service, "_sandbox", lambda url, timeout: fake)
    csv_path, _ = generate(tmp_path, churn=True)
    content = csv_path.read_bytes()
    ids = customer_ids(content)
    typed = ids[3]
    script = iter(
        [
            (
                "run_sql",
                {
                    "sql": "SELECT customer_id, SUM(revenue) AS r FROM data GROUP BY 1 "
                    "ORDER BY 2 DESC LIMIT 5"
                },
            ),
            ("run_python", {"code": "result = df['customer_id'].nunique()"}),
            ("get_segments", {}),
            ("get_churn_risk", {}),
            ("get_insights", {}),
        ]
    )
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        bodies.append(body)
        step = next(script, None)
        if step is not None:
            return httpx.Response(200, content=sse(call_chunk(*step)))
        top = CODE.findall(bodies[1])[0]  # first code in the run_sql result
        text = f"Your top customer is {top}. "
        return httpx.Response(200, content=sse(delta(content=text[:20]), delta(content=text[20:])))

    monkeypatch.setattr(get_settings(), "agent_max_iterations", 8)
    monkeypatch.setattr(chat_service, "get_llm_client", lambda: real_groq(handler, []))
    owner = register(client)
    dataset_id, run, _ = analyze(client, owner, content)
    modules = run["progress"]["modules"]
    assert modules["segmentation"]["status"] == "completed", modules["segmentation"]
    assert modules["churn"]["status"] == "completed", modules["churn"]
    conversation_id = conversation(client, owner, dataset_id)
    events = ask(client, owner, conversation_id, f"How much did {typed.lower()} spend?")

    wire = "\n".join(bodies)
    leaked = [
        i
        for i in ids
        if re.search(rf"(?<![0-9A-Za-z]){re.escape(i)}(?![0-9A-Za-z])", wire, re.IGNORECASE)
    ]
    assert leaked == []
    assert len(CODE.findall(bodies[0])) == 1  # the typed id arrived as its code
    assert fake.calls and not set(ids) & set(fake.calls[-1][1].csv.split())

    final = next(e for e in events if e.type == "final").data
    streamed = "".join(e.data["text"] for e in events if e.type == "token")
    assert "CUST-" not in final["text"] and "CUST-" not in streamed
    top_real = final["text"].removeprefix("Your top customer is ").rstrip(". ")
    assert top_real in ids and streamed.startswith("Your top customer is " + top_real)
    sql_rows = next(
        e.data for e in events if e.type == "tool_result" and e.data["name"] == "run_sql"
    )["evidence"]["rows"]
    assert {r[0] for r in sql_rows} <= set(ids)

    stored = client.get(
        f"{API}/conversations/{conversation_id}/messages", headers=owner.headers
    ).json()
    assert stored[0]["content"] == f"How much did {typed.lower()} spend?"
    answer = [m for m in stored if m["role"] == "assistant"][-1]
    assert top_real in answer["content"] and "CUST-" not in answer["content"]
    shown = json.dumps(stored)
    assert "CUST-" not in shown
