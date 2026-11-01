"""Scripted LLM clients for tests; they need no API key and never touch the network."""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from app.llm.base import LLMError
from app.llm.types import (
    ChatMessage,
    Completion,
    LLMEvent,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
    estimate_tokens,
)

Responder = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class RecordedCall:
    """One `complete_json` request received by the fake client."""

    system: str
    user: str

    def payload(self) -> dict[str, Any]:
        """Return the user message parsed as JSON."""
        parsed: dict[str, Any] = json.loads(self.user)
        return parsed


@dataclass
class RecordedChat:
    """One `chat` request received by the fake client."""

    messages: list[ChatMessage]
    tools: list[ToolSpec]
    model: str


@dataclass
class FakeTurn:
    """One scripted model turn: text, tool calls (args as dict or raw JSON text) or an error."""

    text: str = ""
    tool_calls: list[tuple[str, dict[str, Any] | str]] = field(default_factory=list)
    reasoning: str | None = None
    error: LLMError | None = None
    usage: Usage | None = None
    finish_reason: str | None = None


TurnScript = list[FakeTurn] | Callable[[list[ChatMessage]], FakeTurn]


class FakeLLMClient:
    """Answers `complete_json` with a fixed dict or function and `chat` from a turn script."""

    def __init__(
        self,
        response: dict[str, Any] | Responder | None = None,
        turns: TurnScript | None = None,
    ) -> None:
        """Use `response` for JSON calls and `turns` (a list or a function) for chat calls."""
        self._response = response if response is not None else {"columns": {}}
        self._turns = turns if turns is not None else []
        self._next = 0
        self.calls: list[RecordedCall] = []
        self.chat_calls: list[RecordedChat] = []

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Record the request and return the scripted answer."""
        call = RecordedCall(system=system, user=user)
        self.calls.append(call)
        if callable(self._response):
            return self._response(call.payload())
        return self._response

    def chat(
        self, messages: list[ChatMessage], tools: list[ToolSpec], *, model: str
    ) -> Iterator[LLMEvent]:
        """Replay the next scripted turn as streamed events."""
        self.chat_calls.append(RecordedChat(list(messages), list(tools), model))
        turn = self._turn(messages)
        if turn.error is not None:
            raise turn.error
        yield from stream_turn(turn, messages, model, call_prefix=f"call_{len(self.chat_calls)}")

    def _turn(self, messages: list[ChatMessage]) -> FakeTurn:
        if callable(self._turns):
            return self._turns(messages)
        if self._next < len(self._turns):
            self._next += 1
            return self._turns[self._next - 1]
        return FakeTurn(text="No more scripted turns.")


def stream_turn(
    turn: FakeTurn, messages: list[ChatMessage], model: str, *, call_prefix: str
) -> Iterator[LLMEvent]:
    """Yield a turn as word-sized text deltas and a Completion with estimated usage."""
    words = turn.text.split(" ") if turn.text else []
    for index, word in enumerate(words):
        yield TextDelta(word if index == 0 else f" {word}")
    calls = [
        ToolCall(
            id=f"{call_prefix}_{i}",
            name=name,
            arguments=args if isinstance(args, str) else json.dumps(args),
        )
        for i, (name, args) in enumerate(turn.tool_calls)
    ]
    usage = turn.usage or Usage(
        prompt_tokens=sum(estimate_tokens(m.content) for m in messages),
        completion_tokens=estimate_tokens(turn.text)
        + sum(estimate_tokens(c.arguments) for c in calls),
        estimated=True,
    )
    yield Completion(
        text=turn.text,
        tool_calls=calls,
        reasoning=turn.reasoning,
        usage=usage,
        model=model,
        latency_ms=0,
        finish_reason=turn.finish_reason or ("tool_calls" if calls else "stop"),
    )
