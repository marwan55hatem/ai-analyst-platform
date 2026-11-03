"""Versioned system prompts: every version loads, unknown versions fail clearly, the configured
version is used and stored on each answer, and v3 no longer asks the model to rate itself."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import main
from app.agent import service as chat_service
from app.agent.prompt import build_system_prompt, load_prompt, prompt_versions
from app.core.config import Settings, get_settings
from app.llm.fake import FakeLLMClient, FakeTurn
from app.llm.types import estimate_tokens
from app.models.dataset import Dataset
from tests.agent_helpers import FakeSandbox, ask, sample_chat
from tests.helpers import register
from tests.test_agent_chat import stored

REVENUE_SQL = "SELECT SUM(revenue) AS total FROM data"


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> FakeSandbox:
    fake = FakeSandbox()
    monkeypatch.setattr(chat_service, "_sandbox", lambda url, timeout: fake)
    return fake


def test_every_version_loads() -> None:
    assert {"v1", "v2", "v3"} <= set(prompt_versions())
    for version in ("v1", "v2", "v3"):
        assert "Rules you always follow" in load_prompt(version)


@pytest.mark.parametrize("version", ["v9", "system_v2", "../v2", ""])
def test_unknown_version_fails_with_the_known_list(version: str) -> None:
    with pytest.raises(ValueError, match="Unknown prompt version") as caught:
        load_prompt(version)
    assert "AGENT_PROMPT_VERSION" in str(caught.value) and "v1, v2, v3" in str(caught.value)


def test_setting_defaults_to_v3_and_rejects_other_shapes() -> None:
    assert Settings(_env_file=None).agent_prompt_version == "v3"  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, agent_prompt_version="three")  # type: ignore[call-arg]


def test_unknown_configured_version_stops_the_api_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong = get_settings().model_copy(update={"agent_prompt_version": "v9"})
    monkeypatch.setattr(main, "get_settings", lambda: wrong)
    with pytest.raises(ValueError, match="Unknown prompt version 'v9'"):
        main.create_app()


def test_build_uses_the_requested_version() -> None:
    dataset: Any = Dataset(name="Sales")
    assert build_system_prompt(None, dataset, version="v2").startswith(load_prompt("v2"))
    assert build_system_prompt(None, dataset).startswith(load_prompt("v3"))


def test_v3_never_asks_for_a_self_stated_confidence() -> None:
    v2, v3 = (" ".join(load_prompt(v).split()) for v in ("v2", "v3"))  # ignore line wrapping
    assert "the confidence level (high, medium or low)" in v2
    assert "confidence level (high" not in v3
    assert "Never state a" in v3 and "computed confidence" in v3
    assert "top product by units" in v3 and "average order value" in v3
    assert "2024-11-29" in v3 and "صافي الإيرادات" in v3


def test_v3_stays_within_300_tokens_of_v2() -> None:
    assert estimate_tokens(load_prompt("v3")) - estimate_tokens(load_prompt("v2")) <= 300


def test_answers_store_the_prompt_version(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    model = FakeLLMClient(
        turns=[
            FakeTurn(tool_calls=[("run_sql", {"sql": REVENUE_SQL})]),
            FakeTurn(text="Here is the total."),
        ]
    )
    monkeypatch.setattr(chat_service, "get_llm_client", lambda: model)
    owner = register(client)
    _, conversation_id = sample_chat(client, owner)
    ask(client, owner, conversation_id, "Total revenue?")
    rows = stored(db, conversation_id)
    by_role = {
        role: [r.prompt_version for r in rows if r.role == role] for role in ("user", "tool")
    }
    assert [r.prompt_version for r in rows if r.role == "assistant"] == ["v3", "v3"]
    assert by_role == {"user": [None], "tool": [None]}
