"""Groq presets and the client's groq dialect against a mocked HTTP transport; no network is used."""

import json
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.llm import build_real_client
from app.llm.base import (
    LLMAuthError,
    LLMModelNotFoundError,
    LLMRateLimitError,
    LLMRequestTooLargeError,
    LLMTimeoutError,
    LLMToolCallError,
)
from app.llm.openai_compatible import OpenAICompatibleClient
from app.llm.types import ChatMessage, Completion, RateLimitWait, TextDelta, ToolCall
from tests.test_llm_client import TOOL, Handler, delta, sse

MODEL = "openai/gpt-oss-120b"


def groq(handler: Handler, **kwargs: Any) -> tuple[OpenAICompatibleClient, list[float]]:
    sleeps: list[float] = []
    made = OpenAICompatibleClient(
        base_url="https://api.example.test",
        api_key="test-key",
        dialect="groq",
        key_env="GROQ_API_KEY",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        **kwargs,
    )
    return made, sleeps


def run(made: OpenAICompatibleClient, messages: list[ChatMessage] | None = None) -> list[Any]:
    return list(made.chat(messages or [ChatMessage("user", "hi")], [TOOL], model=MODEL))


def failure(status: int, code: str = "", headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status, headers=headers, json={"error": {"message": "nope", "type": "x", "code": code}}
    )


# -- settings -------------------------------------------------------------------------------------


def test_groq_preset_fills_unset_values() -> None:
    settings = Settings(_env_file=None, llm_provider="groq", llm_base_url="")  # type: ignore[call-arg]
    assert settings.llm_base_url == "https://api.groq.com/openai/v1"
    assert settings.llm_model == MODEL and settings.llm_model_strong == MODEL
    assert settings.llm_max_output_tokens == 3000 and settings.llm_max_prompt_tokens == 5000
    assert settings.agent_tool_output_chars == 5000
    assert settings.llm_price_input_per_m == 0.15
    assert settings.llm_price_strong_output_per_m == 0.60
    assert settings.llm_reasoning_effort == "medium"


def test_explicit_values_beat_the_preset() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="GROQ",
        llm_model="openai/gpt-oss-20b",
        llm_max_output_tokens=1000,
    )
    assert settings.llm_model == "openai/gpt-oss-20b" and settings.llm_max_output_tokens == 1000
    assert settings.llm_model_strong == MODEL


def test_compose_style_empty_strings_fall_back_to_the_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("LLM_MODEL", "LLM_MAX_OUTPUT_TOKENS", "LLM_PRICE_INPUT_PER_M", "LLM_BASE_URL"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.llm_model == MODEL and settings.llm_max_output_tokens == 3000
    assert settings.llm_price_input_per_m == 0.15
    assert settings.llm_base_url == "https://api.groq.com/openai/v1"


def test_deepseek_defaults_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_MODEL", "LLM_MAX_OUTPUT_TOKENS", "LLM_BASE_URL"):
        monkeypatch.setenv(name, "")
    settings = Settings(_env_file=None, llm_provider="deepseek")  # type: ignore[call-arg]
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_model == "deepseek-flash" and settings.llm_model_strong == "deepseek-v4-pro"
    assert settings.llm_max_output_tokens == 4096 and settings.llm_max_prompt_tokens == 0
    assert settings.agent_tool_output_chars == 12_000


def test_groq_never_uses_llm_api_key() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="groq",
        llm_api_key="deepseek-key",
        groq_api_key="",
    )
    with pytest.raises(LLMAuthError, match="GROQ_API_KEY"):
        build_real_client(settings)


def test_groq_key_goes_to_groq_and_is_hidden_from_repr() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="groq",
        llm_api_key="deepseek-key",
        groq_api_key="groq-key",
    )
    assert "groq-key" not in repr(settings)
    made = build_real_client(settings)
    assert made._http.headers["Authorization"] == "Bearer groq-key"
    made.close()


# -- request body and stream parsing --------------------------------------------------------------


def test_groq_body_has_no_deepseek_fields() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        if seen[-1]["stream"]:
            return httpx.Response(200, content=sse(delta(content="ok")))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    made, _ = groq(handler, reasoning_effort="high", max_output_tokens=3000)
    run(made)
    made.complete_json("sys", "user")
    chat, as_json = seen
    assert set(chat) == {
        "model",
        "messages",
        "stream",
        "stream_options",
        "max_completion_tokens",
        "reasoning_effort",
        "include_reasoning",
        "tools",
        "tool_choice",
    }
    assert chat["reasoning_effort"] == "high" and chat["max_completion_tokens"] == 3000
    assert chat["include_reasoning"] is False
    assert as_json["reasoning_effort"] == "low" and as_json["include_reasoning"] is False
    assert as_json["response_format"] == {"type": "json_object"}
    assert "thinking" not in as_json and "max_tokens" not in as_json


def test_groq_never_replays_reasoning() -> None:
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, content=sse(delta(content="ok")))

    made, _ = groq(handler)
    history = [
        ChatMessage("user", "q"),
        ChatMessage(
            "assistant", "", tool_calls=[ToolCall("c1", "run_sql", "{}")], reasoning="private"
        ),
        ChatMessage("tool", "result", tool_call_id="c1"),
    ]
    run(made, history)
    assert b"private" not in seen[0] and b"reasoning_content" not in seen[0]


def test_groq_reasoning_is_never_answer_text() -> None:
    body = sse(
        delta(role="assistant", reasoning="the customer list is"),
        delta(reasoning=" sensitive"),
        delta(content="The answer."),
    )
    made, _ = groq(lambda request: httpx.Response(200, content=body))
    events = run(made)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["The answer."]
    done = events[-1]
    assert isinstance(done, Completion)
    assert done.text == "The answer." and done.reasoning == "the customer list is sensitive"


@pytest.mark.parametrize("where", ["usage", "x_groq"])
def test_groq_usage_from_top_level_and_x_groq(where: str) -> None:
    usage = {
        "prompt_tokens": 1600,
        "completion_tokens": 200,
        "prompt_tokens_details": {"cached_tokens": 1536},
        "completion_tokens_details": {"reasoning_tokens": 50},
    }
    last: dict[str, Any] = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    last.update({"usage": usage} if where == "usage" else {"x_groq": {"usage": usage}})
    made, _ = groq(
        lambda request: httpx.Response(200, content=sse(delta(content="ok"), last)),
    )
    done = run(made)[-1]
    assert (done.usage.prompt_tokens, done.usage.cached_tokens) == (1600, 1536)
    assert (done.usage.completion_tokens, done.usage.reasoning_tokens) == (200, 50)


# -- typed errors ---------------------------------------------------------------------------------


def test_401_names_groq_api_key() -> None:
    made, sleeps = groq(lambda request: failure(401, "invalid_api_key"))
    with pytest.raises(LLMAuthError, match="GROQ_API_KEY"):
        run(made)
    assert sleeps == []


def test_429_waits_then_succeeds_up_to_65_s() -> None:
    answers = iter([failure(429, "rate_limit_exceeded", {"retry-after": "40"})])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(answers, httpx.Response(200, content=sse(delta(content="ok"))))

    made, sleeps = groq(handler)
    events = run(made)
    assert sleeps == [40.0]
    assert [e for e in events if isinstance(e, RateLimitWait)] == [RateLimitWait(40)]
    assert events[-1].text == "ok"


def test_429_fractional_retry_after_rounds_up() -> None:
    made, sleeps = groq(lambda r: failure(429, headers={"retry-after": "7.2"}), max_retries=0)
    with pytest.raises(LLMRateLimitError, match="8 seconds") as caught:
        run(made)
    assert caught.value.retry_after_s == 8 and sleeps == []


def test_429_over_the_wait_cap_fails_at_once_with_seconds() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return failure(429, headers={"retry-after": "90"})

    made, sleeps = groq(handler)
    with pytest.raises(LLMRateLimitError, match="Try again in 90 seconds") as caught:
        run(made)
    assert caught.value.retry_after_s == 90 and sleeps == [] and len(calls) == 1


def test_429_long_wait_uses_minutes() -> None:
    made, _ = groq(lambda r: failure(429, headers={"retry-after": "600"}))
    with pytest.raises(LLMRateLimitError, match="about 10 minutes"):
        run(made)


def test_429_unreadable_retry_after_keeps_the_generic_message() -> None:
    made, _ = groq(lambda r: failure(429, headers={"retry-after": "soon"}), max_retries=0)
    with pytest.raises(LLMRateLimitError, match="in a minute"):
        run(made)


def test_413_maps_to_too_large_and_client_does_not_retry() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return failure(413, "rate_limit_exceeded")

    made, sleeps = groq(handler)
    with pytest.raises(LLMRequestTooLargeError):
        run(made)
    assert len(calls) == 1 and sleeps == []


def test_context_length_exceeded_is_too_large() -> None:
    made, _ = groq(lambda r: failure(400, "context_length_exceeded"))
    with pytest.raises(LLMRequestTooLargeError):
        run(made)


@pytest.mark.parametrize(
    ("status", "code"), [(404, "model_not_found"), (404, ""), (400, "model_decommissioned")]
)
def test_404_and_model_decommissioned_map_to_model_not_found(status: int, code: str) -> None:
    made, sleeps = groq(lambda r: failure(status, code))
    with pytest.raises(LLMModelNotFoundError, match="LLM_MODEL"):
        run(made)
    assert sleeps == []


def test_timeout_is_typed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    made, sleeps = groq(handler)
    with pytest.raises(LLMTimeoutError):
        run(made)
    assert sleeps == []


def test_tool_use_failed_http_400_and_in_stream() -> None:
    made, _ = groq(lambda r: failure(400, "tool_use_failed"))
    with pytest.raises(LLMToolCallError):
        run(made)
    chunk = {"error": {"message": "bad call", "code": "tool_use_failed", "failed_generation": "x"}}
    body = f"data: {json.dumps(chunk)}\n\n".encode()
    made, _ = groq(lambda r: httpx.Response(200, content=body))
    with pytest.raises(LLMToolCallError):
        run(made)


def test_rate_limit_inside_a_stream_is_typed_but_not_retried() -> None:
    chunk = {"error": {"message": "slow down", "code": "rate_limit_exceeded"}}
    body = f"data: {json.dumps(chunk)}\n\n".encode()
    made, sleeps = groq(lambda r: httpx.Response(200, content=body))
    with pytest.raises(LLMRateLimitError):
        run(made)
    assert sleeps == []
