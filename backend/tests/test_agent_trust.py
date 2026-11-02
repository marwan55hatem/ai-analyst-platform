"""What the answer says about its own reliability: confidence, checked numbers, stored charts."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.agent import service as chat_service
from app.agent.confidence import answer_confidence
from app.agent.loop import Record
from app.agent.verifier import count_checked_numbers
from app.llm.fake import FakeLLMClient, FakeTurn
from tests.agent_helpers import FakeSandbox, ask, sample_chat
from tests.helpers import register
from tests.test_agent_stop_retry import stored

SQL = "SELECT 'a' AS name, 1 AS n UNION ALL SELECT 'b', 2"


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> FakeSandbox:
    fake = FakeSandbox()
    monkeypatch.setattr(chat_service, "_sandbox", lambda url, timeout: fake)
    return fake


def tool_record(evidence: dict[str, Any]) -> Record:
    return Record(role="tool", content="", evidence={"ok": True, **evidence})


def test_answer_confidence_is_the_weakest_item() -> None:
    records = [
        tool_record(
            {
                "insights": [
                    {"confidence": "high", "sample_size": 900},
                    {"confidence": "medium", "sample_size": 120},
                ]
            }
        ),
        tool_record({"scenarios": [{"confidence": "low", "sample_size": 40}]}),
    ]
    assert answer_confidence(records) == {"level": "low", "sample_size": 40}


def test_forecast_views_count_even_without_a_sample_size() -> None:
    records = [tool_record({"forecasts": [{"confidence": "medium"}]})]
    assert answer_confidence(records) == {"level": "medium", "sample_size": None}


def test_sql_only_answer_has_no_confidence() -> None:
    assert answer_confidence([tool_record({"sql": "SELECT 1", "rows": [[1]]})]) is None


def test_failed_tools_do_not_count() -> None:
    records = [
        Record(
            role="tool",
            content="",
            evidence={"ok": False, "insights": [{"confidence": "low", "sample_size": 1}]},
        )
    ]
    assert answer_confidence(records) is None


def test_numbers_checked_excludes_exempt_numbers() -> None:
    text = "Top 5 items in 2024 made 1,234.50 and 7% of sales, up from the 9,000 you asked about."
    assert count_checked_numbers(text, "Why 9,000?") == 2


def test_stored_tool_row_keeps_the_chart(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    turns = [
        FakeTurn(tool_calls=[("make_chart", {"sql": SQL, "title": "Names"})]),
        FakeTurn(text="Here it is."),
    ]
    monkeypatch.setattr(chat_service, "get_llm_client", lambda: FakeLLMClient(turns=turns))
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)

    ask(client, owner, conversation_id, "chart it")

    tool = next(r for r in stored(db, conversation_id) if r.role == "tool")
    assert tool.evidence["chart"]["type"] == "bar"
    assert tool.evidence["summary"] == "bar chart"


def test_final_event_and_stored_row_carry_the_trust_fields(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    turns = [
        FakeTurn(tool_calls=[("run_sql", {"sql": "SELECT 1234.5 AS total"})]),
        FakeTurn(text="The total is 1,234.5."),
    ]
    monkeypatch.setattr(chat_service, "get_llm_client", lambda: FakeLLMClient(turns=turns))
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)

    final = ask(client, owner, conversation_id, "total?")[-1]

    assert final.data["numbers_checked"] == 1
    assert final.data["confidence"] is None
    last = stored(db, conversation_id)[-1]
    assert last.evidence["numbers_checked"] == 1
    assert last.evidence["confidence"] is None
    assert last.evidence["untraced_numbers"] == []
    assert last.evidence["scripted"] is False
