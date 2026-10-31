"""Live tests against the real provider (Groq, or DeepSeek without a Groq key). Never run in CI.

Skipped unless RUN_LIVE_LLM=1. The API key is read from the repository's .env file (the test suite
otherwise forces LLM_PROVIDER=fake and an empty key) and is never printed. Run with:

    $env:RUN_LIVE_LLM='1'; uv run --directory backend pytest -m live_llm -v
"""

import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.service import ChatSession
from app.agent.verifier import extract_numbers
from app.core.config import Settings
from app.db.repository import UserRepository
from app.db.session import get_sessionmaker
from app.llm import build_real_client
from app.llm.base import LLMAuthError
from app.llm.openai_compatible import OpenAICompatibleClient
from app.llm.types import ChatMessage, Completion, TextDelta, ToolSpec
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
PACE_S = 20.0  # questions at this pace stay inside Groq's 8,000 tokens per minute


def env_values() -> dict[str, str]:
    """Return the repository .env; `LLM_MODEL` there may belong to another provider."""
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip().lower()] = value.strip().strip('"').strip("'")
    return values


def live_settings() -> Settings:
    """Settings for the real provider (process overrides from conftest are bypassed)."""
    values = env_values()
    if values.get("groq_api_key"):
        return Settings(
            _env_file=None,  # type: ignore[call-arg]
            llm_provider="groq",
            groq_api_key=values["groq_api_key"],
            llm_base_url="",
        )
    keep = {k: v for k, v in values.items() if k.startswith(("llm_", "agent_"))}
    if not keep.get("llm_api_key"):
        pytest.skip("neither GROQ_API_KEY nor LLM_API_KEY is set in .env")
    return Settings(_env_file=None, **{**keep, "llm_provider": "deepseek"})  # type: ignore[arg-type]


def live_client(settings: Settings) -> OpenAICompatibleClient:
    return build_real_client(settings)


def test_live_streams_text_and_reports_usage() -> None:
    settings = live_settings()
    client = live_client(settings)
    messages = [ChatMessage("user", "Reply with one short sentence about sales.")]
    events = list(client.chat(messages, [], model=settings.llm_model))
    done = events[-1]
    assert isinstance(done, Completion) and done.usage.prompt_tokens > 0
    assert done.text and done.text == "".join(e.text for e in events if isinstance(e, TextDelta))
    client.close()


def test_live_replies_in_arabic() -> None:
    settings = live_settings()
    client = live_client(settings)
    messages = [
        ChatMessage("system", "Reply in the user's language."),
        ChatMessage("user", "ما هي عاصمة مصر؟ أجب بجملة واحدة."),
    ]
    done = list(client.chat(messages, [], model=settings.llm_model))[-1]
    assert isinstance(done, Completion) and re.search(r"[؀-ۿ]", done.text)
    client.close()


def test_live_bad_key_is_a_typed_error() -> None:
    settings = live_settings()
    wrong = {"groq_api_key": "wrong-key", "llm_api_key": "wrong-key"}
    client = live_client(settings.model_copy(update=wrong))
    with pytest.raises(LLMAuthError):
        list(client.chat([ChatMessage("user", "hi")], [], model=settings.llm_model))
    client.close()


def test_live_tool_call_round_trip() -> None:
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
        time.sleep(PACE_S)
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
