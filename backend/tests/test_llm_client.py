"""OpenAICompatibleClient (DeepSeek) against a mocked HTTP transport; no network is used."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.llm.base import (
    LLMAuthError,
    LLMBadRequestError,
    LLMError,
    LLMNoBalanceError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.openai_compatible import OpenAICompatibleClient
from app.llm.types import ChatMessage, Completion, TextDelta, ToolCall, ToolSpec

NO_BALANCE = (
    b'{"error":{"message":"Insufficient Balance","type":"insufficient_balance_error",'
    b'"param":null,"code":"invalid_request_error"}}'
)
TOOL = ToolSpec("run_sql", "Run SQL", {"type": "object", "properties": {"sql": {"type": "string"}}})
Handler = Callable[[httpx.Request], httpx.Response]


def sse(*chunks: dict[str, Any], keep_alive: bool = True) -> bytes:
    """Build a DeepSeek-style stream with keep-alive comments and a [DONE] marker."""
    lines = [": keep-alive", ""] if keep_alive else []
    for chunk in chunks:
        lines += [f"data: {json.dumps(chunk)}", ""]
    lines += ["data: [DONE]", ""]
    return "\n".join(lines).encode()


def delta(**fields: Any) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": None}]}


def client(handler: Handler, **kwargs: Any) -> tuple[OpenAICompatibleClient, list[float]]:
    sleeps: list[float] = []
    made = OpenAICompatibleClient(
        base_url="https://api.example.test",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        **kwargs,
    )
    return made, sleeps


def run(made: OpenAICompatibleClient, messages: list[ChatMessage] | None = None) -> list[Any]:
    return list(made.chat(messages or [ChatMessage("user", "hi")], [TOOL], model="deepseek-flash"))


def test_stream_joins_text_reasoning_tool_calls_and_usage() -> None:
    body = sse(
        delta(role="assistant", reasoning_content="think "),
        delta(reasoning_content="more"),
        delta(content="Net "),
        delta(content="revenue"),
        delta(
            tool_calls=[
                {
                    "index": 0,
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run_sql", "arguments": '{"sq'},
                }
            ]
        ),
        delta(tool_calls=[{"index": 0, "function": {"arguments": 'l": "SELECT 1"}'}}]),
        delta(
            tool_calls=[
                {
                    "index": 1,
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "get_insights", "arguments": "{}"},
                }
            ]
        ),
        {
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "prompt_cache_hit_tokens": 100,
                "prompt_cache_miss_tokens": 20,
                "completion_tokens_details": {"reasoning_tokens": 12},
            },
        },
    )
    made, _ = client(lambda request: httpx.Response(200, content=body))
    events = run(made)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Net ", "revenue"]
    done = events[-1]
    assert isinstance(done, Completion)
    assert done.text == "Net revenue" and done.reasoning == "think more"
    assert done.tool_calls == [
        ToolCall("call_1", "run_sql", '{"sql": "SELECT 1"}'),
        ToolCall("call_2", "get_insights", "{}"),
    ]
    assert (done.usage.prompt_tokens, done.usage.cached_tokens) == (120, 100)
    assert (done.usage.completion_tokens, done.usage.reasoning_tokens) == (30, 12)
    assert done.finish_reason == "tool_calls" and done.usage.total_tokens == 150


def test_request_body_replays_reasoning_and_never_forces_a_tool() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.url.path == "/chat/completions"
        return httpx.Response(200, content=sse(delta(content="ok")))

    made, _ = client(handler, thinking=True)
    history = [
        ChatMessage("system", "rules"),
        ChatMessage("user", "q"),
        ChatMessage("assistant", "", tool_calls=[ToolCall("c1", "run_sql", "{}")], reasoning="r1"),
        ChatMessage("tool", "<<<TOOL_OUTPUT>>>", tool_call_id="c1"),
        ChatMessage("assistant", "answer", reasoning="r2"),
        ChatMessage("user", "next"),
    ]
    run(made, history)
    body = seen[0]
    assert body["thinking"] == {"type": "enabled"}
    assert body["tool_choice"] == "auto" and body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert "temperature" not in body
    assistants = [m for m in body["messages"] if m["role"] == "assistant"]
    assert [m["reasoning_content"] for m in assistants] == ["r1", "r2"]
    assert assistants[0]["content"] is None and assistants[0]["tool_calls"][0]["id"] == "c1"
    assert body["messages"][3] == {
        "role": "tool",
        "content": "<<<TOOL_OUTPUT>>>",
        "tool_call_id": "c1",
    }


def test_thinking_disabled_sends_no_reasoning() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=sse(delta(content="ok")))

    made, _ = client(handler, thinking=False)
    run(made, [ChatMessage("assistant", "a", reasoning="secret"), ChatMessage("user", "q")])
    assert seen[0]["thinking"] == {"type": "disabled"}
    assert "reasoning_content" not in seen[0]["messages"][0]


def test_insufficient_balance_402_is_a_clear_typed_error_and_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(402, content=NO_BALANCE)

    made, sleeps = client(handler)
    with pytest.raises(LLMNoBalanceError) as caught:
        run(made)
    assert caught.value.code == "provider_no_balance"
    assert "no balance" in caught.value.user_message.lower()
    assert "Insufficient" not in caught.value.user_message  # provider text is logged, not shown
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, LLMAuthError), (400, LLMBadRequestError), (422, LLMBadRequestError), (418, LLMError)],
)
def test_non_retryable_statuses_fail_at_once(status: int, error: type[LLMError]) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, json={"error": {"message": "nope", "type": "x"}})

    made, sleeps = client(handler)
    with pytest.raises(error):
        run(made)
    assert len(calls) == 1 and sleeps == []


def test_429_and_503_are_retried_with_backoff_then_succeed() -> None:
    statuses = iter([429, 503])

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses, 200)
        if status != 200:
            return httpx.Response(status, headers={"Retry-After": "2"} if status == 429 else {})
        return httpx.Response(200, content=sse(delta(content="ok")))

    made, sleeps = client(handler, max_retries=3)
    events = run(made)
    assert events[-1].text == "ok"
    assert sleeps[0] == pytest.approx(2.0)  # Retry-After honoured
    assert len(sleeps) == 2 and 0 < sleeps[1] <= 30


def test_retries_are_bounded() -> None:
    made, sleeps = client(lambda request: httpx.Response(500), max_retries=2)
    with pytest.raises(LLMUnavailableError):
        run(made)
    assert len(sleeps) == 2


def test_rate_limit_error_after_retries() -> None:
    made, sleeps = client(lambda request: httpx.Response(429), max_retries=1)
    with pytest.raises(LLMRateLimitError) as caught:
        run(made)
    assert caught.value.retryable and len(sleeps) == 1


def test_connection_errors_are_retried_then_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    made, sleeps = client(handler, max_retries=2)
    with pytest.raises(LLMUnavailableError):
        run(made)
    assert len(sleeps) == 2


def test_timeouts_are_typed_and_not_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    made, sleeps = client(handler)
    with pytest.raises(LLMTimeoutError):
        run(made)
    assert sleeps == []


def test_a_broken_stream_is_never_retried_after_text_was_shown() -> None:
    calls: list[int] = []

    class Broken(httpx.SyncByteStream):
        def __iter__(self):  # type: ignore[no-untyped-def]
            yield f"data: {json.dumps(delta(content='partial'))}\n\n".encode()
            raise httpx.RemoteProtocolError("connection lost")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, stream=Broken())

    made, sleeps = client(handler, max_retries=3)
    shown: list[str] = []
    with pytest.raises(LLMUnavailableError):
        for event in made.chat([ChatMessage("user", "q")], [], model="m"):
            if isinstance(event, TextDelta):
                shown.append(event.text)
    assert shown == ["partial"] and len(calls) == 1 and sleeps == []


def test_error_chunk_and_garbage_in_stream() -> None:
    made, _ = client(
        lambda r: httpx.Response(200, content=b'data: {"error": {"message": "x"}}\n\n')
    )
    with pytest.raises(LLMError):
        run(made)
    made, _ = client(lambda r: httpx.Response(200, content=b"data: not-json\n\n"))
    with pytest.raises(LLMError, match="unreadable"):
        run(made)


def test_stream_without_usage_or_tools_still_completes() -> None:
    made, _ = client(
        lambda r: httpx.Response(200, content=sse(delta(content="hi"), keep_alive=False))
    )
    done = run(made)[-1]
    assert done.text == "hi" and done.tool_calls == [] and done.usage.total_tokens == 0


def test_empty_key_is_refused_before_any_request() -> None:
    with pytest.raises(LLMAuthError, match="LLM_API_KEY"):
        OpenAICompatibleClient(base_url="https://x.test", api_key="")


def test_complete_json_success_retry_and_errors() -> None:
    answers = iter(
        [
            httpx.Response(503),
            httpx.Response(200, json={"choices": [{"message": {"content": '{"columns": {}}'}}]}),
        ]
    )
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return next(answers)

    made, sleeps = client(handler)
    assert made.complete_json("sys", "user") == {"columns": {}}
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert seen[0]["thinking"] == {"type": "disabled"} and len(sleeps) == 1

    made, _ = client(lambda r: httpx.Response(402, content=NO_BALANCE))
    with pytest.raises(LLMNoBalanceError):
        made.complete_json("s", "u")
    made, _ = client(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "no"}}]})
    )
    with pytest.raises(LLMError, match="valid JSON"):
        made.complete_json("s", "u")
    made, _ = client(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "[1]"}}]})
    )
    with pytest.raises(LLMError, match="object"):
        made.complete_json("s", "u")

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    made, _ = client(timeout)
    with pytest.raises(LLMTimeoutError):
        made.complete_json("s", "u")

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    made, sleeps = client(refused, max_retries=1)
    with pytest.raises(LLMUnavailableError):
        made.complete_json("s", "u")
    assert len(sleeps) == 1
    made.close()
