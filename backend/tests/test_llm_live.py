"""Live tests against the real provider (DeepSeek). Never run in CI.

Skipped unless RUN_LIVE_LLM=1. The API key is read from the repository's .env file (the test suite
otherwise forces LLM_PROVIDER=fake and an empty key) and is never printed. Run with:

    $env:RUN_LIVE_LLM='1'; uv run --directory backend pytest -m live_llm -v
"""

import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.service import ChatSession
from app.agent.verifier import extract_numbers
from app.core.config import Settings, get_settings
from app.db.repository import UserRepository
from app.db.session import get_sessionmaker
from app.llm.openai_compatible import OpenAICompatibleClient
from app.llm.types import ChatMessage, Completion, ToolSpec
from tests.agent_helpers import conversation
from tests.dataset_helpers import upload_ready
from tests.ground_truth_fixtures import generator
from tests.helpers import API, register

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_LLM") != "1", reason="set RUN_LIVE_LLM=1 to call the real model"
    ),
]
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def live_settings() -> Settings:
    """Settings from the repository .env (process overrides from conftest are bypassed)."""
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip().lower()] = value.strip().strip('"').strip("'")
    keep = {k: v for k, v in values.items() if k.startswith(("llm_", "agent_"))}
    if not keep.get("llm_api_key"):
        pytest.skip("LLM_API_KEY is not set in .env")
    return get_settings().model_copy(update={**keep, "llm_provider": "deepseek"})


def live_client(settings: Settings) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        thinking=settings.llm_thinking == "enabled",
        timeout_s=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        max_output_tokens=settings.llm_max_output_tokens,
    )


def test_live_tool_call_round_trip_with_reasoning_replay() -> None:
    settings = live_settings()
    client = live_client(settings)
    tool = ToolSpec(
        "run_sql",
        "Run one SELECT on table data(region, revenue).",
        {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
            "additionalProperties": False,
        },
    )
    messages = [
        ChatMessage("system", "Use the tool for every number."),
        ChatMessage("user", "What is the total revenue? Use run_sql."),
    ]
    first = [e for e in client.chat(messages, [tool], model=settings.llm_model)][-1]
    assert isinstance(first, Completion) and first.tool_calls and first.usage.total_tokens > 0
    messages.append(
        ChatMessage("assistant", first.text, tool_calls=first.tool_calls, reasoning=first.reasoning)
    )
    messages.append(
        ChatMessage("tool", '{"rows": [[1234.5]]}', tool_call_id=first.tool_calls[0].id)
    )
    second = [e for e in client.chat(messages, [tool], model=settings.llm_model)][-1]
    assert isinstance(second, Completion) and "1234" in second.text.replace(",", "")
    client.close()


def _expected(facts: dict[str, Any]) -> list[tuple[str, Any]]:
    return [
        ("What is our total net revenue?", facts["total_revenue"]),
        ("How many orders are in the data?", facts["order_count"]),
        ("What is the average order value?", facts["aov_cents"] / 100),
        ("How many distinct customers are there?", facts["customer_count"]),
        ("How many distinct products do we sell?", facts["product_count"]),
        ("Which product has the highest revenue?", facts["top_product"]),
        ("Which region brings the most revenue?", facts["top_region"]),
        ("What is our gross margin in percent?", facts["margin_pct"]),
        ("What was revenue in December 2024?", facts["monthly_revenue_cents"]["2024-12"] / 100),
        ("Which calendar month has the highest revenue in total?", "December"),
    ]


def _matches(text: str, expected: Any) -> bool:
    if isinstance(expected, str):
        return expected.lower() in text.lower()
    return any(
        abs(n.value - expected) <= max(0.01 * abs(expected), 0.5) for n in extract_numbers(text)
    )


def test_live_ten_questions_match_ground_truth(client: TestClient, tmp_path: Path) -> None:
    settings = live_settings()
    csv_path, _, facts = generator().generate(
        tmp_path, years=3, churn=True, elasticity=True, drivers=True, anomalies=True
    )
    owner = register(client)
    dataset = upload_ready(client, owner, csv_path.read_bytes(), "planted.csv")
    assert (
        client.post(
            f"{API}/datasets/{dataset['id']}/analyze", headers=owner.headers, json={}
        ).status_code
        == 202
    )
    conversation_id = conversation(client, owner, dataset["id"])
    with get_sessionmaker()() as db:
        user = UserRepository(db, uuid.UUID(owner.tenant_id)).get(uuid.UUID(owner.user_id))
    assert user is not None
    llm = live_client(settings)
    report: list[tuple[str, bool, int]] = []
    for question, expected in _expected(facts):
        session = ChatSession(
            user, uuid.UUID(conversation_id), question, settings=settings, llm=llm
        )
        events = list(session.events())
        final = next((e.data for e in events if e.type == "final"), None)
        assert final is not None, [e.data for e in events if e.type == "error"]
        evidence = sum(e.type == "tool_result" for e in events)
        report.append((question, _matches(final["text"], expected), evidence))
    llm.close()
    print("\n".join(f"{'PASS' if ok else 'FAIL'} tools={n} {q}" for q, ok, n in report))
    assert all(ok and n > 0 for _, ok, n in report), report
