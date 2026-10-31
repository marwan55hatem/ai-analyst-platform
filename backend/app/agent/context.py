"""Keeps a model call inside the provider's prompt limit by trimming older tool results."""

import json
from collections.abc import Collection

from app.agent.untrusted import CLOSE
from app.llm.types import ChatMessage, ToolCall, ToolSpec, estimate_tokens

TRIM_NOTE = "... [trimmed to fit the model's limit; full result kept as evidence]"


def is_replayable(call: ToolCall, tool_names: Collection[str]) -> bool:
    """Return whether a provider will accept this call in a conversation's history.

    Groq refuses every request whose history names a tool that is not on offer, so a bad call must
    never be sent back. Arguments that are JSON but fail the schema are fine: those are ordinary
    tool errors the model can recover from.
    """
    if call.name not in tool_names:
        return False
    try:
        json.loads(call.arguments or "{}")
    except ValueError:
        return False
    return True


def prompt_estimate(messages: list[ChatMessage], specs: list[ToolSpec]) -> int:
    """Return the estimated prompt tokens of a call: messages, tool calls and full tool specs."""
    total = sum(estimate_tokens(m.content) for m in messages)
    total += sum(estimate_tokens(c.arguments) for m in messages for c in m.tool_calls)
    return total + estimate_tokens(json.dumps([s.to_openai() for s in specs]))


def shrink_tool_results(
    messages: list[ChatMessage], keep_chars: int, *, spare_last: int = 0
) -> bool:
    """Cut tool results to `keep_chars`, skipping the last `spare_last`; return whether any changed.

    The opening line and the closing delimiter always survive, so trimmed data stays inside its
    block and can never read as instructions.
    """
    tools = [m for m in messages if m.role == "tool"]
    changed = False
    for message in tools[: len(tools) - spare_last] if spare_last else tools:
        trimmed = _shrink(message.content, keep_chars)
        if trimmed != message.content:
            message.content = trimmed
            changed = True
    return changed


def _shrink(content: str, keep_chars: int) -> str:
    if len(content) <= keep_chars or TRIM_NOTE in content:
        return content
    return f"{content[:keep_chars]}{TRIM_NOTE}\n{CLOSE}"
