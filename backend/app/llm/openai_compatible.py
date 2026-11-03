"""OpenAI-compatible chat client (DeepSeek, Groq) over plain httpx, with streaming and tool calls.

DeepSeek specifics handled here:
- `thinking: {"type": "enabled"|"disabled"}`; with tools, the `reasoning_content` of every earlier
  assistant message must be sent back or the API answers 400, so reasoning is replayed.
- `tool_choice` stays `auto` (required/named choices are rejected in thinking mode).
- Streams may contain `: keep-alive` comment lines while the request waits; they are skipped.
- Usage arrives on the last chunk with `stream_options.include_usage`.

Groq specifics (the `groq` dialect):
- `max_completion_tokens` replaces the deprecated `max_tokens`; `reasoning_effort` replaces
  `thinking`. `include_reasoning: false` keeps reasoning out of responses, and it is never replayed.
- A malformed or hallucinated tool call comes back as the error code `tool_use_failed`, as an HTTP
  400 or as an `error` chunk inside the stream.
- 429 carries `Retry-After`; rate limits refill continuously, so most waits are under a minute.
- Every response carries `x-ratelimit-remaining-tokens` and `x-ratelimit-reset-tokens` (the
  tokens-per-minute window, read when the response starts). The answer's output tokens are
  taken off once it ends. Before the next request the client waits for the window when the
  estimated prompt would not fit (Groq does not count the output cap when it admits a request),
  so the agent's calls within one answer do not run into a 429.
"""

import json
import math
import random
import re
import time
from collections.abc import Callable, Iterator
from typing import Any, Literal

import httpx
import structlog

from app.llm.base import (
    LLMAuthError,
    LLMBadRequestError,
    LLMError,
    LLMModelNotFoundError,
    LLMNoBalanceError,
    LLMRateLimitError,
    LLMRequestTooLargeError,
    LLMTimeoutError,
    LLMToolCallError,
    LLMUnavailableError,
)
from app.llm.types import (
    ChatMessage,
    Completion,
    LLMEvent,
    RateLimitWait,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
    estimate_tokens,
)

logger = structlog.get_logger()

MAX_BACKOFF_S = 30.0
MAX_RATE_LIMIT_WAIT_S = 65
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
Dialect = Literal["deepseek", "groq"]
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_DURATION_UNITS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}


class OpenAICompatibleClient:
    """Chat completions client; one instance can be shared by threads (httpx.Client is safe)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        thinking: bool = True,
        timeout_s: float = 120.0,
        max_retries: int = 3,
        max_output_tokens: int = 4096,
        json_model: str = "deepseek-flash",
        dialect: Dialect = "deepseek",
        reasoning_effort: str = "medium",
        key_env: str = "LLM_API_KEY",
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Configure the client; `transport`, `sleep` and `clock` let tests mock the I/O."""
        if not api_key:
            raise LLMAuthError(f"{key_env} is empty; set it in .env to use the real provider.")
        self._thinking = thinking
        self._max_retries = max_retries
        self._max_output_tokens = max_output_tokens
        self._json_model = json_model
        self._dialect = dialect
        self._reasoning_effort = reasoning_effort
        self._key_env = key_env
        self._sleep = sleep
        self._clock = clock
        # Tokens left in the provider's per-minute window and when it is full again (monotonic).
        self._window: tuple[int, float] | None = None
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            transport=transport,
        )

    def close(self) -> None:
        """Close the HTTP connection pool."""
        self._http.close()

    # -- public API -----------------------------------------------------------------------------

    def chat(
        self, messages: list[ChatMessage], tools: list[ToolSpec], *, model: str
    ) -> Iterator[LLMEvent]:
        """Stream one turn: text deltas, then one Completion with tool calls and usage."""
        body = self._chat_body(messages, tools, model)
        started = time.monotonic()
        streaming = False
        for attempt in range(self._max_retries + 1):
            pause = self._window_wait(body)
            if pause > 0:
                yield RateLimitWait(math.ceil(pause))
                self._sleep(pause)
            try:
                with self._http.stream("POST", "/chat/completions", json=body) as response:
                    self._note_window(response)
                    if response.status_code != 200:
                        error = _map_error(
                            response.status_code, response.read(), response, self._key_env
                        )
                        if not self._should_retry(error, attempt):
                            raise error
                        delay = self._delay(attempt, response, error)
                    else:
                        # From the first byte on, nothing is retried: text may already be shown.
                        streaming = True
                        for event in _parse_stream(response, model, started, self._key_env):
                            if isinstance(event, Completion):
                                self._spend_window(event.usage.completion_tokens)
                            yield event
                        return
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError() from exc
            except httpx.TransportError as exc:
                error = LLMUnavailableError()
                if not streaming and self._should_retry(error, attempt):
                    self._backoff(attempt, None)
                    continue
                raise error from exc
            if isinstance(error, LLMRateLimitError):
                yield RateLimitWait(math.ceil(delay))
            self._sleep(delay)
        raise LLMUnavailableError()  # pragma: no cover - the loop always returns or raises

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Return a JSON object answer (non-streaming, least reasoning, json_object format)."""
        body = {
            "model": self._json_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
            **self._provider_params(json_mode=True),
        }
        for attempt in range(self._max_retries + 1):
            pause = self._window_wait(body)
            if pause > 0:
                self._sleep(pause)
            try:
                response = self._http.post("/chat/completions", json=body)
                self._note_window(response)
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError() from exc
            except httpx.TransportError as exc:
                if attempt < self._max_retries:
                    self._backoff(attempt, None)
                    continue
                raise LLMUnavailableError() from exc
            if response.status_code != 200:
                error = _map_error(response.status_code, response.content, response, self._key_env)
                if self._should_retry(error, attempt):
                    self._backoff(attempt, response, error)
                    continue
                raise error
            return _json_answer(response.content)
        raise LLMUnavailableError()  # pragma: no cover - the loop always returns or raises

    # -- helpers --------------------------------------------------------------------------------

    def _chat_body(
        self, messages: list[ChatMessage], tools: list[ToolSpec], model: str
    ) -> dict[str, Any]:
        replay = self._dialect == "deepseek" and self._thinking and bool(tools)
        body: dict[str, Any] = {
            "model": model,
            "messages": [m.to_openai(include_reasoning=replay) for m in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            **self._provider_params(json_mode=False),
        }
        if tools:
            body["tools"] = [t.to_openai() for t in tools]
            body["tool_choice"] = "auto"
        return body

    def _provider_params(self, *, json_mode: bool) -> dict[str, Any]:
        """Return the output cap and reasoning parameters in the provider's own terms."""
        if self._dialect == "groq":
            return {
                "max_completion_tokens": self._max_output_tokens,
                "reasoning_effort": "low" if json_mode else self._reasoning_effort,
                "include_reasoning": False,
            }
        thinking = self._thinking and not json_mode
        return {
            "max_tokens": self._max_output_tokens,
            "thinking": {"type": "enabled" if thinking else "disabled"},
        }

    def _note_window(self, response: httpx.Response) -> None:
        """Remember the provider's tokens-per-minute window from the rate-limit headers."""
        remaining = response.headers.get("x-ratelimit-remaining-tokens")
        reset = _duration_s(response.headers.get("x-ratelimit-reset-tokens"))
        if remaining is None or reset is None:
            return
        try:
            self._window = (int(float(remaining)), self._clock() + reset)
        except ValueError:
            return

    def _spend_window(self, tokens: int) -> None:
        """Take an answer's output tokens off the window (the headers predate the output)."""
        if self._window is not None:
            remaining, full_at = self._window
            self._window = (remaining - tokens, full_at)

    def _window_wait(self, body: dict[str, Any]) -> float:
        """Return the seconds to wait for the per-minute window (0 when the request fits)."""
        if self._window is None:
            return 0.0
        remaining, full_at = self._window
        left = full_at - self._clock()
        if left <= 0:
            return 0.0
        needed = estimate_tokens(json.dumps(body, ensure_ascii=False))
        if needed <= remaining:
            return 0.0
        logger.info("llm_tpm_wait", needed=needed, remaining=remaining, wait_s=round(left, 2))
        return min(float(MAX_RATE_LIMIT_WAIT_S), left)

    def _should_retry(self, error: LLMError, attempt: int) -> bool:
        if isinstance(error, LLMRateLimitError):
            if (error.retry_after_s or 0) > MAX_RATE_LIMIT_WAIT_S:
                return False  # a daily limit: waiting here would only hold the request open
        return error.retryable and attempt < self._max_retries

    def _delay(
        self, attempt: int, response: httpx.Response | None, error: LLMError | None = None
    ) -> float:
        cap = min(MAX_BACKOFF_S, 0.5 * 2.0**attempt)
        delay = cap * (0.5 + random.random())  # noqa: S311
        seconds = _retry_after(response)
        if seconds is not None:
            limited = isinstance(error, LLMRateLimitError)
            delay = min(MAX_RATE_LIMIT_WAIT_S if limited else MAX_BACKOFF_S, seconds)
        logger.warning("llm_retry", attempt=attempt + 1, delay_s=round(delay, 2))
        return delay

    def _backoff(
        self, attempt: int, response: httpx.Response | None, error: LLMError | None = None
    ) -> None:
        self._sleep(self._delay(attempt, response, error))


def _duration_s(raw: str | None) -> float | None:
    """Parse a rate-limit reset such as `7.66s`, `1m2.5s`, `120ms` or `2` (seconds)."""
    if not raw:
        return None
    text = raw.strip()
    try:
        return float(text)
    except ValueError:
        pass
    parts = _DURATION_PART.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text:
        return None
    return sum(float(number) * _DURATION_UNITS[unit] for number, unit in parts)


def _retry_after(response: httpx.Response | None) -> int | None:
    """Return the Retry-After header in whole seconds, rounded up."""
    raw = response.headers.get("Retry-After") if response is not None else None
    try:
        return math.ceil(float(raw)) if raw else None
    except ValueError:
        return None


def _error_code(body: bytes) -> str:
    """Return the provider's machine-readable error code, if the body has one."""
    try:
        parsed = json.loads(body or b"{}")
    except ValueError:
        return ""
    error = parsed.get("error") if isinstance(parsed, dict) else None
    return str(error.get("code") or "") if isinstance(error, dict) else ""


def _map_error(
    status: int,
    body: bytes,
    response: httpx.Response | None = None,
    key_env: str = "LLM_API_KEY",
) -> LLMError:
    """Map a provider error to a typed error (the provider's message is logged, not shown).

    The body's code is read first: in-stream errors carry no HTTP status, and several failures
    share 400.
    """
    detail = _error_detail(body)
    code = _error_code(body)
    logger.warning("llm_http_error", status=status, provider_code=code, provider_error=detail)
    if code == "tool_use_failed":
        return LLMToolCallError(status=status)
    if code in ("model_not_found", "model_decommissioned") or status == 404:
        return LLMModelNotFoundError(status=status)
    if code == "context_length_exceeded" or status == 413:
        return LLMRequestTooLargeError(status=status)
    if code == "rate_limit_exceeded" or status == 429:
        return LLMRateLimitError(status=status, retry_after_s=_retry_after(response))
    if status == 401:
        return LLMAuthError(
            f"The model provider rejected the API key. Check {key_env}.", status=status
        )
    if status == 402:
        return LLMNoBalanceError(status=status)
    if status in (400, 422):
        return LLMBadRequestError(status=status)
    if status in RETRYABLE_STATUS:
        return LLMUnavailableError(status=status)
    return LLMError(status=status)


def _error_detail(body: bytes) -> str:
    try:
        parsed = json.loads(body or b"{}")
    except ValueError:
        return body[:200].decode(errors="replace")
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, dict):
        return f"{error.get('type', '')}: {error.get('message', '')}"[:200]
    return str(parsed)[:200]


def _parse_stream(
    response: httpx.Response, model: str, started: float, key_env: str
) -> Iterator[LLMEvent]:
    text: list[str] = []
    reasoning: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    usage = Usage()
    finish: str | None = None
    for line in response.iter_lines():
        if not line or line.startswith(":"):
            continue  # blank separators and `: keep-alive` comments
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = _load_chunk(data)
        if "error" in chunk:
            raise _map_error(int(chunk.get("status") or 500), data.encode(), key_env=key_env)
        raw_usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
        if raw_usage:
            usage = _usage(raw_usage)
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            thought = delta.get("reasoning_content") or delta.get("reasoning")
            if thought:
                reasoning.append(thought)
            if delta.get("content"):
                text.append(delta["content"])
                yield TextDelta(delta["content"])
            for part in delta.get("tool_calls") or []:
                _merge_call(calls, part)
            finish = choice.get("finish_reason") or finish
    tool_calls = [
        ToolCall(id=c["id"] or f"call_{i}", name=c["name"], arguments=c["arguments"] or "{}")
        for i, c in sorted(calls.items())
    ]
    yield Completion(
        text="".join(text),
        tool_calls=tool_calls,
        reasoning="".join(reasoning) if reasoning else None,
        usage=usage,
        model=model,
        latency_ms=int((time.monotonic() - started) * 1000),
        finish_reason=finish,
    )


def _load_chunk(data: str) -> dict[str, Any]:
    try:
        chunk = json.loads(data)
    except ValueError as exc:
        raise LLMError("The model provider sent an unreadable stream.") from exc
    if not isinstance(chunk, dict):
        raise LLMError("The model provider sent an unreadable stream.")
    return chunk


def _merge_call(calls: dict[int, dict[str, str]], part: dict[str, Any]) -> None:
    """Join streamed tool-call fragments: the first carries id and name, later ones arguments."""
    index = int(part.get("index", 0))
    entry = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
    if part.get("id"):
        entry["id"] = part["id"]
    function = part.get("function") or {}
    if function.get("name"):
        entry["name"] = function["name"]
    if function.get("arguments"):
        entry["arguments"] += function["arguments"]


def _usage(raw: dict[str, Any]) -> Usage:
    details = raw.get("completion_tokens_details") or {}
    cached = raw.get("prompt_cache_hit_tokens")
    if cached is None:
        cached = (raw.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return Usage(
        prompt_tokens=int(raw.get("prompt_tokens") or 0),
        cached_tokens=int(cached or 0),
        completion_tokens=int(raw.get("completion_tokens") or 0),
        reasoning_tokens=int(details.get("reasoning_tokens") or 0),
    )


def _json_answer(body: bytes) -> dict[str, Any]:
    try:
        content = json.loads(body)["choices"][0]["message"]["content"]
        answer = json.loads(content)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMError("The model did not return valid JSON.") from exc
    if not isinstance(answer, dict):
        raise LLMError("The model did not return a JSON object.")
    return answer
