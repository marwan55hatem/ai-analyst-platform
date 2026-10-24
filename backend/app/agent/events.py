"""Typed events of the chat stream and their Server-Sent Events encoding."""

import json
import math
from dataclasses import dataclass
from typing import Any, Literal

EventType = Literal["token", "tool_call", "tool_result", "chart", "caveat", "final", "error"]


@dataclass(frozen=True)
class AgentEvent:
    """One event: its type and a JSON-serialisable payload."""

    type: EventType
    data: dict[str, Any]

    def sse(self) -> str:
        """Return the event as an SSE frame (`event:` line, one `data:` line, blank line)."""
        payload = json.dumps(jsonable(self.data), ensure_ascii=False, allow_nan=False)
        return f"event: {self.type}\ndata: {payload}\n\n"


def token(text: str) -> AgentEvent:
    """Build a token event (a piece of answer text)."""
    return AgentEvent("token", {"text": text})


def error(code: str, message: str) -> AgentEvent:
    """Build a terminal error event; the stream ends after it."""
    return AgentEvent("error", {"code": code, "message": message})


def caveat(kind: str, message: str, **extra: Any) -> AgentEvent:
    """Build a caveat event the user should see next to the answer."""
    return AgentEvent("caveat", {"kind": kind, "message": message, **extra})


def jsonable(value: Any) -> Any:
    """Return a strict-JSON copy: non-finite floats become null, unknown types strings."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)
