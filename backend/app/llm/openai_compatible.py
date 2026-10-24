"""OpenAI-compatible chat client (DeepSeek) over plain httpx, with streaming and tool calls.

DeepSeek specifics handled here:
- `thinking: {"type": "enabled"|"disabled"}`; with tools, the `reasoning_content` of every earlier
  assistant message must be sent back or the API answers 400, so reasoning is replayed.
- `tool_choice` stays `auto` (required/named choices are rejected in thinking mode).
- Streams may contain `: keep-alive` comment lines while the request waits; they are skipped.
- Usage arrives on the last chunk with `stream_options.include_usage`.
"""

import json
import random
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import structlog

from app.llm.base import (
    LLMAuthError,
    LLMBadRequestError,
    LLMError,
    LLMNoBalanceError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.types import ChatMessage, Completion, LLMEvent, TextDelta, ToolCall, ToolSpec, Usage

logger = structlog.get_logger()

MAX_BACKOFF_S = 30.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


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
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Configure the client; `transport` lets tests mock the HTTP layer."""
        if not api_key:
            raise LLMAuthError("LLM_API_KEY is empty; set it in .env to use the real provider.")
        self._thinking = thinking
        self._max_retries = max_retries
        self._max_output_tokens = max_output_tokens
        self._json_model = json_model
        self._sleep = sleep
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
            try:
                with self._http.stream("POST", "/chat/completions", json=body) as response:
                    if response.status_code != 200:
                        error = _map_error(response.status_code, response.read(), response)
                        if self._should_retry(error, attempt):
                            self._backoff(attempt, response)
                            continue
                        raise error
                    # From the first byte on, nothing is retried: text may already be shown.
                    streaming = True
                    yield from _parse_stream(response, model, started)
                    return
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError() from exc
            except httpx.TransportError as exc:
                error = LLMUnavailableError()
                if not streaming and self._should_retry(error, attempt):
                    self._backoff(attempt, None)
                    continue
                raise error from exc
        raise LLMUnavailableError()  # pragma: no cover - the loop always returns or raises

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Return a JSON object answer (non-streaming, thinking off, json_object format)."""
        body = {
            "model": self._json_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "max_tokens": self._max_output_tokens,
            "stream": False,
        }
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.post("/chat/completions", json=body)
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError() from exc
            except httpx.TransportError as exc:
                if attempt < self._max_retries:
                    self._backoff(attempt, None)
                    continue
                raise LLMUnavailableError() from exc
            if response.status_code != 200:
                error = _map_error(response.status_code, response.content, response)
                if self._should_retry(error, attempt):
                    self._backoff(attempt, response)
                    continue
                raise error
            return _json_answer(response.content)
        raise LLMUnavailableError()  # pragma: no cover - the loop always returns or raises

    # -- helpers --------------------------------------------------------------------------------

    def _chat_body(
        self, messages: list[ChatMessage], tools: list[ToolSpec], model: str
    ) -> dict[str, Any]:
        replay = self._thinking and bool(tools)
        body: dict[str, Any] = {
            "model": model,
            "messages": [m.to_openai(include_reasoning=replay) for m in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": self._max_output_tokens,
            "thinking": {"type": "enabled" if self._thinking else "disabled"},
        }
        if tools:
            body["tools"] = [t.to_openai() for t in tools]
            body["tool_choice"] = "auto"
        return body

    def _should_retry(self, error: LLMError, attempt: int) -> bool:
        return error.retryable and attempt < self._max_retries

    def _backoff(self, attempt: int, response: httpx.Response | None) -> None:
        delay = min(MAX_BACKOFF_S, 0.5 * 2**attempt) * (0.5 + random.random())  # noqa: S311
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after and retry_after.strip().isdigit():
            delay = min(MAX_BACKOFF_S, float(retry_after))
        logger.warning("llm_retry", attempt=attempt + 1, delay_s=round(delay, 2))
        self._sleep(delay)


def _map_error(status: int, body: bytes, response: httpx.Response | None = None) -> LLMError:
    """Map an HTTP error status to a typed error (the provider's message is logged, not shown)."""
    detail = _error_detail(body)
    logger.warning("llm_http_error", status=status, provider_error=detail)
    if status == 401:
        return LLMAuthError(status=status)
    if status == 402:
        return LLMNoBalanceError(status=status)
    if status == 429:
        return LLMRateLimitError(status=status)
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


def _parse_stream(response: httpx.Response, model: str, started: float) -> Iterator[LLMEvent]:
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
            raise _map_error(int(chunk.get("status") or 500), data.encode())
        if chunk.get("usage"):
            usage = _usage(chunk["usage"])
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("reasoning_content"):
                reasoning.append(delta["reasoning_content"])
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
