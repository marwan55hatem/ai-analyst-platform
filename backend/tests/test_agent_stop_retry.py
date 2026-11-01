"""Stopping an answer, retrying a question and one turn per conversation."""

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import service as chat_service
from app.agent.service import ChatSession, history_messages
from app.agent.streams import get_stream_slots
from app.agent.tools import TOOLS_BY_NAME
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.llm.base import LLMUnavailableError
from app.llm.fake import FakeLLMClient, FakeTurn
from app.llm.types import estimate_tokens
from app.models.agent import AgentUsageDaily, Message
from tests.agent_helpers import FakeSandbox, ask, parse_sse, sample_chat
from tests.helpers import API, Account, register

SQL = "SELECT 1 AS one"


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> FakeSandbox:
    fake = FakeSandbox()
    monkeypatch.setattr(chat_service, "_sandbox", lambda url, timeout: fake)
    return fake


def use_model(monkeypatch: pytest.MonkeyPatch, model: Any) -> Any:
    monkeypatch.setattr(chat_service, "get_llm_client", lambda: model)
    return model


def stored(db: Session, conversation_id: str) -> list[Message]:
    db.expire_all()
    query = select(Message).where(Message.conversation_id == uuid.UUID(conversation_id))
    return list(db.scalars(query.order_by(Message.seq)))


def session_for(account: Account, conversation_id: str, llm: Any, question: str) -> ChatSession:
    user = SimpleNamespace(tenant_id=uuid.UUID(account.tenant_id), id=uuid.UUID(account.user_id))
    return ChatSession(
        user,  # type: ignore[arg-type]
        uuid.UUID(conversation_id),
        question,
        llm=llm,
        sandbox=FakeSandbox(),
        session_factory=get_sessionmaker(),
    )


def stop_after(session: ChatSession, frames: int) -> None:
    """Read some frames, then leave as a client pressing Stop does."""
    stream = session.frames()
    for _ in range(frames):
        next(stream)
    stream.close()


def retry(client: TestClient, account: Account, conversation_id: str, message: str) -> Any:
    return client.post(
        f"{API}/chat/stream",
        headers=account.headers,
        json={"conversation_id": conversation_id, "message": message, "retry": True},
    )


# -- stop -----------------------------------------------------------------------------------------


def test_closing_mid_answer_settles_budget_and_keeps_partial(
    client: TestClient, db: Session, sandbox: FakeSandbox
) -> None:
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)
    llm = FakeLLMClient(turns=[FakeTurn(text="one two three four five six")])

    stop_after(session_for(owner, conversation_id, llm, "hi"), 3)

    rows = stored(db, conversation_id)
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[1].content == "one two three"
    assert rows[1].evidence["partial"] == "stopped"
    assert "untraced_numbers" not in rows[1].evidence
    sent = llm.chat_calls[0]
    prompt = sum(estimate_tokens(m.content) for m in sent.messages)
    schemas = estimate_tokens(json.dumps([s.parameters for s in sent.tools]))
    used = db.scalar(select(AgentUsageDaily.tokens))
    assert used == prompt + schemas + estimate_tokens("one two three")


def test_stop_during_a_tool_round_keeps_the_turn_marked_stopped(
    client: TestClient, db: Session, sandbox: FakeSandbox
) -> None:
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)
    calls = [("run_sql", {"sql": SQL}), ("run_sql", {"sql": SQL})]
    llm = FakeLLMClient(turns=[FakeTurn(tool_calls=calls), FakeTurn(text="never")])

    stop_after(session_for(owner, conversation_id, llm, "hi"), 1)

    rows = stored(db, conversation_id)
    assert rows[-1].role == "assistant" and rows[-1].evidence["partial"] == "stopped"
    assert rows[-1].content == ""


def test_provider_error_after_text_keeps_partial_as_failed(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    class BreaksMidway(FakeLLMClient):
        def chat(self, messages, tools, *, model):  # type: ignore[no-untyped-def]
            from app.llm.types import TextDelta

            yield TextDelta("part of an answer")
            raise LLMUnavailableError("down")

    use_model(monkeypatch, BreaksMidway())
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)

    events = ask(client, owner, conversation_id, "hi")

    assert events[-1].type == "error"
    rows = stored(db, conversation_id)
    assert rows[-1].content == "part of an answer"
    assert rows[-1].evidence["partial"] == "failed"


def test_finished_answer_is_not_marked_partial(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    use_model(monkeypatch, FakeLLMClient(turns=[FakeTurn(text="done")]))
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)

    ask(client, owner, conversation_id, "hi")

    assert "partial" not in stored(db, conversation_id)[-1].evidence


def test_partial_answers_are_not_replayed(
    client: TestClient, db: Session, sandbox: FakeSandbox
) -> None:
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)
    llm = FakeLLMClient(turns=[FakeTurn(text="unchecked 123 figure")])
    stop_after(session_for(owner, conversation_id, llm, "first"), 2)

    replayed = history_messages(stored(db, conversation_id))

    assert [m.role for m in replayed] == ["user"]


def test_unanswered_tool_calls_are_dropped_from_history(
    client: TestClient, db: Session, sandbox: FakeSandbox
) -> None:
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)
    calls = [("run_sql", {"sql": SQL}), ("run_sql", {"sql": SQL})]
    llm = FakeLLMClient(turns=[FakeTurn(text="Checking.", tool_calls=calls)])
    # Frames: one token, tool_call and tool_result of the first call, then the second tool_call.
    stop_after(session_for(owner, conversation_id, llm, "first"), 4)

    replayed = history_messages(stored(db, conversation_id))

    assistant = next(m for m in replayed if m.role == "assistant")
    answered = {m.tool_call_id for m in replayed if m.role == "tool"}
    assert len(assistant.tool_calls) == 1 and {c.id for c in assistant.tool_calls} == answered
    assert set(TOOLS_BY_NAME) >= {c.name for c in assistant.tool_calls}


# -- retry ----------------------------------------------------------------------------------------


def test_retry_reuses_the_question_and_clears_the_failed_attempt(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)
    use_model(
        monkeypatch,
        FakeLLMClient(
            turns=[
                FakeTurn(tool_calls=[("run_sql", {"sql": SQL})]),
                FakeTurn(error=LLMUnavailableError("down")),
                FakeTurn(text="The answer."),
            ]
        ),
    )
    assert ask(client, owner, conversation_id, "How much?")[-1].type == "error"
    title = client.get(f"{API}/conversations/{conversation_id}", headers=owner.headers).json()

    response = retry(client, owner, conversation_id, "How much?")

    assert parse_sse(response.text)[-1].type == "final"
    rows = stored(db, conversation_id)
    assert [r.role for r in rows] == ["user", "assistant"]
    assert [r.seq for r in rows] == sorted({r.seq for r in rows})
    assert rows[-1].content == "The answer."
    after = client.get(f"{API}/conversations/{conversation_id}", headers=owner.headers).json()
    assert after["title"] == title["title"]


def test_retry_of_an_answered_question_returns_already_answered(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    llm = use_model(monkeypatch, FakeLLMClient(turns=[FakeTurn(text="Done.")]))
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)
    ask(client, owner, conversation_id, "How much?")

    events = parse_sse(retry(client, owner, conversation_id, "How much?").text)

    assert [e.type for e in events] == ["error"]
    assert events[0].data["code"] == "already_answered"
    assert len(llm.chat_calls) == 1
    assert [r.role for r in stored(db, conversation_id)] == ["user", "assistant"]


def test_retry_with_different_text_is_a_new_question(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    use_model(monkeypatch, FakeLLMClient(turns=[FakeTurn(text="A."), FakeTurn(text="B.")]))
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)
    ask(client, owner, conversation_id, "First?")

    retry(client, owner, conversation_id, "Second?")

    rows = stored(db, conversation_id)
    assert [r.content for r in rows if r.role == "user"] == ["First?", "Second?"]


def test_retry_when_the_question_never_arrived_stores_it_once(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    use_model(monkeypatch, FakeLLMClient(turns=[FakeTurn(text="A.")]))
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)

    retry(client, owner, conversation_id, "First?")

    assert [r.role for r in stored(db, conversation_id)] == ["user", "assistant"]


# -- one turn per conversation --------------------------------------------------------------------


def test_second_turn_in_a_conversation_gets_conversation_busy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    use_model(monkeypatch, FakeLLMClient(turns=[FakeTurn(text="A.")]))
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)
    key = uuid.UUID(conversation_id)
    token = get_stream_slots().acquire(key, 1)  # the same slot a running turn holds

    response = client.post(
        f"{API}/chat/stream",
        headers=owner.headers,
        json={"conversation_id": conversation_id, "message": "again"},
    )

    get_stream_slots().release(key, token)
    assert response.status_code == 429
    assert response.json()["code"] == "conversation_busy"
    assert response.headers["retry-after"] == "2"
    assert ask(client, owner, conversation_id, "again")[-1].type == "final"


def test_conversation_slot_is_released_after_an_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    turns = [FakeTurn(error=LLMUnavailableError("down")), FakeTurn(text="A.")]
    use_model(monkeypatch, FakeLLMClient(turns=turns))
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)

    assert ask(client, owner, conversation_id, "first")[-1].type == "error"
    assert ask(client, owner, conversation_id, "second")[-1].type == "final"


def test_second_conversation_is_not_blocked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    use_model(monkeypatch, FakeLLMClient(turns=[FakeTurn(text="A.")]))
    owner = register(client)
    dataset_id, first = sample_chat(client, owner)
    second = client.post(
        f"{API}/conversations", headers=owner.headers, json={"dataset_id": dataset_id}
    ).json()["id"]
    key = uuid.UUID(first)
    token = get_stream_slots().acquire(key, 1)

    events = ask(client, owner, second, "hi")

    get_stream_slots().release(key, token)
    assert events[-1].type == "final"


def test_settings_cap_still_counts_user_streams(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)
    limit = get_settings().chat_max_concurrent_streams
    tokens = [get_stream_slots().acquire(uuid.UUID(owner.user_id), limit) for _ in range(limit)]

    response = client.post(
        f"{API}/chat/stream",
        headers=owner.headers,
        json={"conversation_id": conversation_id, "message": "x"},
    )

    for token in tokens:
        get_stream_slots().release(uuid.UUID(owner.user_id), token)
    assert response.json()["code"] == "too_many_streams"
    # The conversation's slot was not left taken by the refused request.
    token = get_stream_slots().acquire(uuid.UUID(conversation_id), 1)
    get_stream_slots().release(uuid.UUID(conversation_id), token)
