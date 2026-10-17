"""A scripted LLM client for tests and local runs; it needs no API key."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Responder = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class RecordedCall:
    """One request received by the fake client."""

    system: str
    user: str

    def payload(self) -> dict[str, Any]:
        """Return the user message parsed as JSON."""
        parsed: dict[str, Any] = json.loads(self.user)
        return parsed


class FakeLLMClient:
    """Answers with a fixed dict or a function of the request, and records every call."""

    def __init__(self, response: dict[str, Any] | Responder | None = None) -> None:
        """Use `response` as the answer (a callable receives the parsed request)."""
        self._response = response if response is not None else {"columns": {}}
        self.calls: list[RecordedCall] = []

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Record the request and return the scripted answer."""
        call = RecordedCall(system=system, user=user)
        self.calls.append(call)
        if callable(self._response):
            return self._response(call.payload())
        return self._response
