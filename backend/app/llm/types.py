"""Provider-neutral chat types: messages, tool specs, usage and streamed events."""

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolCall:
    """A tool call requested by the model; `arguments` is the raw JSON text it produced."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ToolSpec:
    """A tool offered to the model: name, description and JSON schema of its arguments."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        """Return the OpenAI-compatible `tools[]` entry."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ChatMessage:
    """One message of the conversation sent to the model."""

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    reasoning: str | None = None

    def to_openai(self, *, include_reasoning: bool) -> dict[str, Any]:
        """Return the OpenAI-compatible message (reasoning is replayed for thinking mode)."""
        message: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.role == "tool":
            message["tool_call_id"] = self.tool_call_id
        if self.role == "assistant":
            if self.tool_calls:
                message["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                    for call in self.tool_calls
                ]
                message["content"] = self.content or None
            if include_reasoning and self.reasoning is not None:
                message["reasoning_content"] = self.reasoning
        return message


@dataclass
class Usage:
    """Token counts of one model call; `estimated` is set when the provider sent none."""

    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        """Return prompt plus completion tokens (reasoning is part of completion)."""
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class TextDelta:
    """A piece of the answer text, streamed as it arrives."""

    text: str


@dataclass(frozen=True)
class Completion:
    """The end of one model call: full text, tool calls, reasoning, usage and latency."""

    text: str
    tool_calls: list[ToolCall]
    reasoning: str | None
    usage: Usage
    model: str
    latency_ms: int
    finish_reason: str | None = None


LLMEvent = TextDelta | Completion


def estimate_tokens(text: str) -> int:
    """Return a rough token count (about 4 characters per token, at least 1 for non-empty text)."""
    return (len(text) + 3) // 4 if text else 0
