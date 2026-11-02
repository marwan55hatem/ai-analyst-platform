"""AI usage endpoint, the conversation filter and the scripted paths the chat UI is tested with."""

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.agent import service as chat_service
from app.agent.budget import TokenBudget
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.llm.fake import FakeLLMClient, FakeTurn
from app.llm.scripted import ScriptedAnalyst
from tests.agent_helpers import FakeSandbox, ask, conversation, kinds, sample_chat
from tests.helpers import API, register


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> FakeSandbox:
    fake = FakeSandbox()
    monkeypatch.setattr(chat_service, "_sandbox", lambda url, timeout: fake)
    return fake


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> ScriptedAnalyst:
    model = ScriptedAnalyst()
    monkeypatch.setattr(ScriptedAnalyst, "sleep", staticmethod(lambda seconds: None))
    monkeypatch.setattr(chat_service, "get_llm_client", lambda: model)
    return model


# -- usage ----------------------------------------------------------------------------------------


def test_usage_counts_only_own_tenant(client: TestClient) -> None:
    first, second = register(client, company="A Corp"), register(client, company="B Corp")
    TokenBudget(get_sessionmaker(), uuid.UUID(first.tenant_id), budget=5_000).reserve(1_234)

    mine = client.get(f"{API}/ai/usage", headers=first.headers).json()
    theirs = client.get(f"{API}/ai/usage", headers=second.headers).json()

    assert mine["tokens_used"] == 1_234
    assert theirs["tokens_used"] == 0
    assert mine["daily_budget"] == get_settings().agent_daily_token_budget


def test_usage_says_when_the_budget_resets(client: TestClient) -> None:
    body = client.get(f"{API}/ai/usage", headers=register(client).headers).json()
    resets = datetime.fromisoformat(body["resets_at"])
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date()
    assert resets.date() == tomorrow and (resets.hour, resets.minute) == (0, 0)


def test_usage_names_the_provider_but_never_a_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = get_settings()
    monkeypatch.setattr(current, "llm_provider", "groq")
    monkeypatch.setattr(current, "llm_model", "openai/gpt-oss-120b")
    monkeypatch.setattr(current, "llm_model_strong", "openai/gpt-oss-120b")
    monkeypatch.setattr(current, "groq_api_key", "gsk_dummy_not_real")
    monkeypatch.setattr(current, "llm_api_key", "sk_dummy_not_real")

    response = client.get(f"{API}/ai/usage", headers=register(client).headers)

    assert response.json()["provider"] == "groq"
    assert response.json()["model"] == "openai/gpt-oss-120b"
    assert "dummy_not_real" not in response.text
    assert not [key for key in response.json() if "key" in key.lower()]


def test_usage_has_no_model_name_without_a_real_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "llm_provider", "fake")
    body = client.get(f"{API}/ai/usage", headers=register(client).headers).json()
    assert body["provider"] == "fake" and body["model"] is None and body["model_strong"] is None


def test_usage_requires_auth(client: TestClient) -> None:
    assert client.get(f"{API}/ai/usage").status_code == 401


# -- conversation filter --------------------------------------------------------------------------


def test_list_filter_returns_only_that_datasets_conversations(client: TestClient) -> None:
    owner = register(client)
    first, in_first = sample_chat(client, owner)
    second, in_second = sample_chat(client, owner)

    found = client.get(f"{API}/conversations?dataset_id={first}", headers=owner.headers).json()

    assert [c["id"] for c in found] == [in_first]
    assert second != first and in_second != in_first


def test_list_filter_returns_empty_for_another_tenants_dataset(client: TestClient) -> None:
    owner_a, owner_b = register(client, company="A Corp"), register(client, company="B Corp")
    dataset_id, _ = sample_chat(client, owner_a)
    found = client.get(f"{API}/conversations?dataset_id={dataset_id}", headers=owner_b.headers)
    assert found.status_code == 200 and found.json() == []


def test_dataset_filter_rejects_a_bad_uuid(client: TestClient) -> None:
    owner = register(client)
    response = client.get(f"{API}/conversations?dataset_id=nope", headers=owner.headers)
    assert response.status_code == 422


# -- scripted paths -------------------------------------------------------------------------------


def test_rate_limit_demo_streams_status_before_the_answer(
    client: TestClient, scripted: ScriptedAnalyst, sandbox: FakeSandbox
) -> None:
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)

    events = ask(client, owner, conversation_id, "[rate-limit-demo] what is the net revenue?")

    assert kinds(events)[:2] == ["status", "tool_call"]
    assert events[0].data == {"code": "rate_limited", "retry_in_s": 3}
    assert sum(e.type == "status" for e in events) == 1
    assert events[-1].type == "final"


def test_slow_demo_paces_every_word(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    pauses: list[float] = []
    model = ScriptedAnalyst()
    monkeypatch.setattr(ScriptedAnalyst, "sleep", staticmethod(pauses.append))
    monkeypatch.setattr(chat_service, "get_llm_client", lambda: model)
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)

    events = ask(client, owner, conversation_id, "[slow-demo] what is the net revenue?")

    tokens = sum(e.type == "token" for e in events)
    assert tokens > 20 and len(pauses) == tokens


def test_table_demo_has_a_wide_table_and_a_long_code_line(
    client: TestClient, scripted: ScriptedAnalyst, sandbox: FakeSandbox
) -> None:
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)

    text = ask(client, owner, conversation_id, "[table-demo] show it")[-1].data["text"]

    assert re.search(r"^\|.*\|.*\|.*\|.*\|.*\|.*\|$", text, re.MULTILINE)
    assert "```" in text
    assert max(len(line) for line in text.splitlines()) > 150


def test_chart_question_makes_a_stored_line_chart(
    client: TestClient, scripted: ScriptedAnalyst, sandbox: FakeSandbox
) -> None:
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)

    events = ask(client, owner, conversation_id, "Show a chart of revenue by month")

    assert [e.data["name"] for e in events if e.type == "tool_call"] == [
        "get_dataset_overview",
        "make_chart",
    ]
    chart = next(e for e in events if e.type == "chart")
    assert chart.data["spec"]["type"] == "line"
    assert events[-1].data["untraced_numbers"] == []


def test_bar_chart_question_ranks_products(
    client: TestClient, scripted: ScriptedAnalyst, sandbox: FakeSandbox
) -> None:
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)

    events = ask(client, owner, conversation_id, "Show a bar chart of the products")

    assert next(e for e in events if e.type == "chart").data["spec"]["type"] == "bar"


def test_arabic_revenue_question_gets_an_arabic_answer_with_traced_numbers(
    client: TestClient, scripted: ScriptedAnalyst, sandbox: FakeSandbox
) -> None:
    owner = register(client)
    dataset_id, _ = sample_chat(client, owner)
    run = client.post(f"{API}/datasets/{dataset_id}/analyze", headers=owner.headers, json={})
    assert run.status_code == 202, run.text
    conversation_id = conversation(client, owner, dataset_id)

    final = ask(client, owner, conversation_id, "ما هو صافي الإيراد؟")[-1].data

    assert "التوصية" in final["text"]
    assert final["untraced_numbers"] == []


def test_real_model_ignores_the_demo_markers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    model = FakeLLMClient(turns=[FakeTurn(text="Plain answer.")])
    monkeypatch.setattr(chat_service, "get_llm_client", lambda: model)
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)

    events = ask(client, owner, conversation_id, "[rate-limit-demo] hi")

    assert "status" not in kinds(events)
