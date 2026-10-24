"""LLM layer units: pricing, router, fake and scripted clients, provider factory and safety rails."""

import json
from typing import Any

import pytest

import app.llm as llm_package
from app.core.config import Settings, get_settings
from app.llm import ScriptedAnalyst, ensure_provider_allowed, get_llm_client, is_scripted
from app.llm.base import LLMError, LLMNoBalanceError, LLMNotConfiguredError
from app.llm.fake import FakeLLMClient, FakeTurn
from app.llm.pricing import approximate_cost_usd
from app.llm.router import choose_model
from app.llm.scripted import INVENTED_NUMBER, SCRIPTED_BANNER, SUMMARY_MARKER
from app.llm.types import ChatMessage, Completion, TextDelta, ToolCall, ToolSpec, Usage

TOOLS = [
    ToolSpec(name, "", {"type": "object", "properties": {}})
    for name in (
        "get_dataset_overview",
        "run_sql",
        "run_python",
        "get_insights",
        "get_forecast",
        "get_anomalies",
        "get_segments",
        "get_churn_risk",
        "explain_drivers",
        "what_if",
        "make_chart",
    )
]


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def completion(events: list[Any]) -> Completion:
    last = events[-1]
    assert isinstance(last, Completion)
    return last


def wrapped(name: str, call_id: str, content: dict[str, Any]) -> ChatMessage:
    body = json.dumps(content)
    text = f'<<<TOOL_OUTPUT tool="{name}" id="{call_id}">>>\n{body}\n<<<END_TOOL_OUTPUT>>>'
    return ChatMessage("tool", text, tool_call_id=call_id)


def converse(question: str, results: dict[str, dict[str, Any]]) -> str:
    """Run the scripted analyst through its plan with the given canned tool results."""
    analyst = ScriptedAnalyst()
    messages = [ChatMessage("system", "rules"), ChatMessage("user", question)]
    for _ in range(4):
        done = completion(list(analyst.chat(messages, TOOLS, model="m")))
        if not done.tool_calls:
            return done.text
        messages.append(ChatMessage("assistant", "", tool_calls=done.tool_calls))
        for call in done.tool_calls:
            messages.append(
                wrapped(
                    call.name, call.id, results.get(call.name, {"error": "x", "message": "missing"})
                )
            )
    raise AssertionError("the scripted analyst did not finish")


# -- pricing and router ---------------------------------------------------------------------------


def test_cost_uses_main_or_strong_prices_and_cached_rate() -> None:
    s = settings()
    usage = Usage(prompt_tokens=1_000_000, cached_tokens=400_000, completion_tokens=1_000_000)
    main = approximate_cost_usd(usage, "deepseek-flash", s)
    strong = approximate_cost_usd(usage, "deepseek-v4-pro", s)
    assert main == pytest.approx(0.6 * 0.44 + 0.4 * 0.014 + 1.32, rel=1e-6)
    assert strong == pytest.approx(0.6 * 1.32 + 0.4 * 0.044 + 3.96, rel=1e-6)
    assert approximate_cost_usd(Usage(), "deepseek-flash", s) == 0


@pytest.mark.parametrize(
    ("question", "strong"),
    [
        ("What is net revenue?", False),
        ("Forecast next quarter", False),
        ("What should I do to grow profit?", True),
        ("Why did sales drop in March?", True),
        ("what if I raise Office prices 10%", True),
        ("ماذا يجب أن أفعل لزيادة الربح؟", True),
        ("كم الإيرادات؟", False),
    ],
)
def test_router_sends_hard_questions_to_the_strong_model(question: str, strong: bool) -> None:
    s = settings()
    assert choose_model(question, s) == ("deepseek-v4-pro" if strong else "deepseek-flash")
    assert choose_model(question, s, "summary") == "deepseek-flash"


def test_router_falls_back_when_models_are_blank() -> None:
    s = settings(llm_model="", llm_model_strong="")
    assert choose_model("what should I do", s) == "deepseek-flash"


# -- fake client ----------------------------------------------------------------------------------


def test_fake_client_replays_turns_records_calls_and_raises_errors() -> None:
    fake = FakeLLMClient(
        turns=[
            FakeTurn(tool_calls=[("run_sql", {"sql": "SELECT 1"}), ("get_insights", "{bad")]),
            FakeTurn(text="two words", reasoning="why"),
            FakeTurn(error=LLMNoBalanceError()),
        ]
    )
    first = completion(list(fake.chat([ChatMessage("user", "hello there")], TOOLS, model="m")))
    assert [c.name for c in first.tool_calls] == ["run_sql", "get_insights"]
    assert first.tool_calls[1].arguments == "{bad" and first.usage.estimated
    events = list(fake.chat([ChatMessage("user", "q")], [], model="m"))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["two", " words"]
    assert completion(events).reasoning == "why"
    with pytest.raises(LLMNoBalanceError):
        list(fake.chat([], [], model="m"))
    assert completion(list(fake.chat([], [], model="m"))).text == "No more scripted turns."
    assert len(fake.chat_calls) == 4 and fake.chat_calls[0].model == "m"


def test_fake_client_accepts_a_function_and_keeps_complete_json() -> None:
    fake = FakeLLMClient(
        response=lambda payload: {"echo": payload}, turns=lambda m: FakeTurn(text=str(len(m)))
    )
    assert completion(list(fake.chat([ChatMessage("user", "a")], [], model="m"))).text == "1"
    assert fake.complete_json("s", '{"x": 1}') == {"echo": {"x": 1}}
    assert fake.calls[0].payload() == {"x": 1}


# -- scripted analyst -----------------------------------------------------------------------------

KPI = {
    "insights": [
        {
            "title": "Net revenue: 1,234,567.89",
            "summary": "Net revenue after returns.",
            "confidence": "high",
            "sample_size": 5000,
            "data": {
                "net_revenue": 1234567.89,
                "gross_revenue": 1300000.0,
                "returns_total": 65432.11,
                "orders": 4100,
                "average_order_value": 301.11,
            },
        }
    ]
}


def test_scripted_revenue_answer_uses_only_tool_numbers_and_says_it_is_scripted() -> None:
    text = converse("What is our net revenue?", {"get_insights": KPI})
    assert text.startswith(SCRIPTED_BANNER)
    for heading in (
        "**Recommendation**",
        "**Why (evidence)**",
        "**Expected impact (range)**",
        "**Risks**",
        "**Next step**",
    ):
        assert heading in text
    assert "1,234,567.89" in text and "4,100" in text


def test_scripted_what_if_parses_target_and_percent() -> None:
    analyst = ScriptedAnalyst()
    messages = [ChatMessage("user", "What if I raise Office prices 10%?")]
    call = completion(list(analyst.chat(messages, TOOLS, model="m"))).tool_calls[0]
    args = json.loads(call.arguments)
    assert call.name == "what_if"
    assert args["scenarios"] == [
        {"lever": "price", "change_pct": 10.0, "scope": "category", "target": "Office"}
    ]
    cut = completion(
        list(analyst.chat([ChatMessage("user", "cut all prices by 5%")], TOOLS, model="m"))
    )
    assert json.loads(cut.tool_calls[0].arguments)["scenarios"][0] == {
        "lever": "price",
        "change_pct": -5.0,
        "scope": "all",
    }
    result = {
        "scenarios": [
            {
                "status": "ok",
                "target": "Office",
                "summary": "Raising prices 10%",
                "confidence": "medium",
                "sample_size": 104,
                "revenue_change": {"low": -100.0, "expected": 2500.5, "high": 5000.0},
                "profit_change": {"low": 10.0, "expected": 3000.0, "high": 6000.0},
                "warnings": [],
            }
        ]
    }
    text = converse("what if I raise Office prices 10%", {"what_if": result})
    assert "2,500.50" in text and "-100.00" in text and "test it before rolling it out" in text
    weak = {
        "scenarios": [
            {
                "status": "not_enough_evidence",
                "message": "No reliable elasticity",
                "warnings": ["w"],
            }
        ]
    }
    assert "cannot answer this reliably" in converse(
        "what if I raise Office prices 10%", {"what_if": weak}
    )


@pytest.mark.parametrize(
    ("question", "tool", "result", "expected"),
    [
        (
            "Forecast revenue",
            "get_forecast",
            {
                "forecasts": [
                    {
                        "periods": ["2026-01-01"],
                        "point": [10.0],
                        "lo80": [8.0],
                        "hi80": [12.0],
                        "model": "ETS",
                        "summary": "s",
                        "confidence": "high",
                    }
                ]
            },
            "80% range 8.00 to 12.00",
        ),
        (
            "Any anomalies?",
            "get_anomalies",
            {"insights": [{"title": "Spike", "summary": "big"}]},
            "Spike: big",
        ),
        (
            "Show segments",
            "get_segments",
            {"insights": [{"title": "Champions", "summary": "loyal"}], "note": "n"},
            "Champions",
        ),
        (
            "Churn risk?",
            "get_churn_risk",
            {
                "insights": [],
                "customers": {
                    "items": [{"customer_key": "cust_1", "probability": 0.834, "risk_band": "high"}]
                },
            },
            "83.4%",
        ),
    ],
)
def test_scripted_answers_for_each_pattern(
    question: str, tool: str, result: dict[str, Any], expected: str
) -> None:
    text = converse(question, {tool: result})
    assert expected in text and text.startswith(SCRIPTED_BANNER)


def test_scripted_plan_answer_picks_the_best_scenario() -> None:
    results = {
        "get_insights": KPI,
        "explain_drivers": {"importance": [{"feature": "price"}, {"feature": "category"}]},
        "what_if": {
            "scenarios": [
                {
                    "status": "ok",
                    "lever": "price",
                    "change_pct": 5,
                    "scope": "all",
                    "summary": "price up",
                    "profit_change": {"low": 1.0, "expected": 50.0, "high": 90.0},
                },
                {
                    "status": "ok",
                    "lever": "cost",
                    "change_pct": -5,
                    "scope": "all",
                    "summary": "cost down",
                    "profit_change": {"low": 2.0, "expected": 80.0, "high": 120.0},
                },
            ]
        },
    }
    text = converse("What should I do to grow profit?", results)
    assert "cost lever" in text and "80.00" in text and "price, category" in text
    results["what_if"] = {"scenarios": []}
    assert "no lever scenario had enough evidence" in converse("what should I do", results)


def test_scripted_top_products_runs_overview_then_sql() -> None:
    overview = {
        "columns": [
            {"name": "product", "role": "product"},
            {"name": "price", "role": "price"},
            {"name": "qty", "role": "quantity"},
        ]
    }
    analyst = ScriptedAnalyst()
    messages = [ChatMessage("user", "Show the top products")]
    first = completion(list(analyst.chat(messages, TOOLS, model="m")))
    assert first.tool_calls[0].name == "get_dataset_overview"
    messages += [
        ChatMessage("assistant", "", tool_calls=first.tool_calls),
        wrapped("get_dataset_overview", first.tool_calls[0].id, overview),
    ]
    second = completion(list(analyst.chat(messages, TOOLS, model="m")))
    sql = json.loads(second.tool_calls[0].arguments)["sql"]
    assert 'SUM("price" * "qty")' in sql
    text = converse(
        "Show the top products",
        {"get_dataset_overview": overview, "run_sql": {"rows": [["Pens", 99.5]], "row_count": 1}},
    )
    assert "Pens: 99.50" in text


def test_scripted_python_verifier_demo_errors_and_clarifying_question() -> None:
    code_q = "run python:\n```python\nresult = 1\n```"
    assert "Sandbox result: 1" in converse(code_q, {"run_python": {"result": 1, "notes": []}})
    refused = converse(code_q, {"run_python": {"error": "code_rejected", "message": "import os"}})
    assert "could not run" in refused
    assert INVENTED_NUMBER in converse("[verifier-demo] revenue", {"get_insights": KPI})
    assert "cannot answer this right now" in converse("net revenue", {})
    clarify = completion(
        list(ScriptedAnalyst().chat([ChatMessage("user", "hmm?")], TOOLS, model="m"))
    )
    assert clarify.tool_calls == [] and "Which of these do you mean?" in clarify.text


def test_scripted_summary_and_json() -> None:
    analyst = ScriptedAnalyst()
    messages = [
        ChatMessage("system", SUMMARY_MARKER + " go"),
        ChatMessage("user", "first q"),
        ChatMessage("user", "second q"),
    ]
    assert (
        completion(list(analyst.chat(messages, [], model="m"))).text
        == "Earlier questions: first q; second q"
    )
    assert analyst.complete_json("s", "u") == {"columns": {}}


def test_scripted_ignores_broken_tool_output() -> None:
    analyst = ScriptedAnalyst()
    messages = [
        ChatMessage("user", "net revenue"),
        ChatMessage("assistant", "", tool_calls=[ToolCall("a", "get_insights", "{}")]),
        ChatMessage("tool", "no delimiters", tool_call_id="a"),
    ]
    assert "Which of these" in completion(list(analyst.chat(messages, TOOLS, model="m"))).text


# -- factory and safety rails ---------------------------------------------------------------------


def test_tests_never_build_a_real_client() -> None:
    current = get_settings()
    assert current.llm_provider == "fake" and current.llm_api_key == ""
    assert isinstance(get_llm_client(), ScriptedAnalyst) and is_scripted()


def test_factory_by_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    current = get_settings()
    monkeypatch.setattr(current, "llm_provider", "none")
    assert get_llm_client() is None
    monkeypatch.setattr(current, "llm_provider", "mystery")
    with pytest.raises(LLMError, match="Unknown"):
        get_llm_client()
    monkeypatch.setattr(current, "llm_provider", "deepseek")
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        get_llm_client()  # empty key in tests: refused before any request
    monkeypatch.setattr(current, "llm_api_key", "dummy-not-used")
    monkeypatch.setattr(llm_package, "_real", {})
    first = get_llm_client()
    assert first is get_llm_client() and type(first).__name__ == "OpenAICompatibleClient"
    monkeypatch.setattr(current, "llm_provider", "fake")
    monkeypatch.setattr(current, "app_env", "production")
    with pytest.raises(LLMNotConfiguredError):
        get_llm_client()


def test_fake_refuses_production_and_warns_otherwise(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(RuntimeError, match="production"):
        ensure_provider_allowed(settings(llm_provider="fake", app_env="production"))
    ensure_provider_allowed(settings(llm_provider="fake", app_env="development"))
    assert "SCRIPTED TEST MODEL" in capsys.readouterr().out
    ensure_provider_allowed(settings(llm_provider="deepseek", app_env="production"))


def test_create_app_refuses_fake_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import create_app

    monkeypatch.setattr(get_settings(), "app_env", "production")
    with pytest.raises(RuntimeError):
        create_app()


def test_message_serialisation_for_tool_messages() -> None:
    message = ChatMessage("tool", "x", tool_call_id="c")
    assert message.to_openai(include_reasoning=True) == {
        "role": "tool",
        "content": "x",
        "tool_call_id": "c",
    }
    assert ToolSpec("t", "d", {"type": "object"}).to_openai()["function"]["name"] == "t"
