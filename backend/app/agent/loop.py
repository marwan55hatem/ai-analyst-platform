"""The agent loop: model turns, validated tool calls, verified numbers, typed events.

One question runs at most `AGENT_MAX_ITERATIONS` model turns with tools; if the model still wants
tools after that, a last turn without tools asks for an answer from the results so far. Every
model turn is paid for out of the tenant's daily budget, reserved before the call.
"""

import json
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

from app.agent import events
from app.agent.budget import BudgetExceededError, TokenBudget
from app.agent.events import AgentEvent
from app.agent.tools import (
    TOOLS_BY_NAME,
    AgentContext,
    Tool,
    ToolArgumentError,
    ToolResult,
    parse_arguments,
)
from app.agent.untrusted import find_injection, wrap_tool_output
from app.agent.verifier import caveat_text, untraced_numbers
from app.core.config import Settings
from app.llm.base import LLMClient, LLMError
from app.llm.pricing import approximate_cost_usd
from app.llm.types import ChatMessage, Completion, TextDelta, ToolCall, Usage, estimate_tokens

logger = structlog.get_logger()

FINAL_ROUND_NOTE = (
    "You have used all tool calls for this question. Answer now from the tool results above, "
    "or say what could not be determined. Do not request more tools."
)
EMPTY_ANSWER = "The model returned no answer. Please rephrase the question."


@dataclass
class Record:
    """A message to persist once the turn ends."""

    role: str
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    caveats: list[dict[str, Any]] = field(default_factory=list)
    reasoning: str | None = None
    model: str | None = None
    usage: Usage = field(default_factory=Usage)
    cost: Decimal = Decimal(0)
    latency_ms: int = 0


@dataclass
class TurnOutcome:
    """What the loop produced: records to persist, totals, and how it ended."""

    records: list[Record] = field(default_factory=list)
    final_text: str = ""
    untraced: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost: Decimal = Decimal(0)
    error: str | None = None
    tool_calls: int = 0


class AgentLoop:
    """Runs one question against an LLM client and the tenant-bound tools."""

    def __init__(
        self,
        llm: LLMClient,
        ctx: AgentContext,
        budget: TokenBudget,
        settings: Settings,
        *,
        model: str,
        tools: tuple[Tool, ...] | None = None,
        scripted: bool = False,
    ) -> None:
        """Bind the loop to a client, a tool context, a budget and the chosen model."""
        self.llm = llm
        self.ctx = ctx
        self.budget = budget
        self.settings = settings
        self.model = model
        self.tools = tools if tools is not None else tuple(TOOLS_BY_NAME.values())
        self.scripted = scripted
        self.outcome = TurnOutcome()

    def run(self, system: str, history: list[ChatMessage], question: str) -> Iterator[AgentEvent]:
        """Yield the events of one question; `self.outcome` holds what to persist."""
        messages = [ChatMessage("system", system), *history, ChatMessage("user", question)]
        specs = [tool.spec() for tool in self.tools]
        by_name = {tool.name: tool for tool in self.tools}
        seen_results: list[Any] = []
        limit = self.settings.agent_max_iterations
        for iteration in range(limit + 1):
            last_round = iteration == limit
            if last_round:
                messages.append(ChatMessage("system", FINAL_ROUND_NOTE))
            completion = yield from self._model_turn(messages, [] if last_round else specs)
            if completion is None:
                return
            if not completion.tool_calls or last_round:
                self.outcome.final_text = completion.text.strip() or EMPTY_ANSWER
                break
            for call in completion.tool_calls:
                result = yield from self._tool_call(call, by_name)
                seen_results.append(result.content)
                wrapped = wrap_tool_output(
                    call.name, call.id, result.content, self.settings.agent_tool_output_chars
                )
                messages.append(ChatMessage("tool", wrapped, tool_call_id=call.id))
                self.outcome.records.append(
                    Record(
                        role="tool",
                        content=wrapped,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        evidence={"ok": result.ok, **result.evidence},
                        caveats=result.caveats,
                    )
                )
        yield from self._finish(seen_results, question)

    def _model_turn(
        self, messages: list[ChatMessage], specs: list[Any]
    ) -> Generator[AgentEvent, None, Completion | None]:
        """Reserve budget, stream one model turn, record it; return the Completion or None."""
        estimate = sum(
            estimate_tokens(m.content) + estimate_tokens(m.reasoning or "") for m in messages
        )
        estimate += (
            estimate_tokens(json.dumps([s.parameters for s in specs]))
            + self.settings.llm_max_output_tokens
        )
        try:
            reserved = self.budget.reserve(estimate)
        except BudgetExceededError as exc:
            self.outcome.error = exc.code
            yield events.error(exc.code, exc.message)
            return None
        completion: Completion | None = None
        try:
            for event in self.llm.chat(messages, specs, model=self.model):
                if isinstance(event, TextDelta):
                    if event.text:
                        yield events.token(event.text)
                else:
                    completion = event
        except LLMError as exc:
            self.budget.settle(reserved, 0)
            self.outcome.error = exc.code
            logger.warning("agent_llm_error", code=exc.code)
            yield events.error(exc.code, exc.user_message)
            return None
        except Exception:
            self.budget.settle(reserved, 0)
            raise
        if completion is None:
            self.budget.settle(reserved, 0)
            self.outcome.error = "provider_error"
            yield events.error("provider_error", "The model stream ended without a result.")
            return None
        self.budget.settle(reserved, completion.usage.total_tokens)
        cost = Decimal(str(approximate_cost_usd(completion.usage, completion.model, self.settings)))
        self.outcome.tokens_in += completion.usage.prompt_tokens
        self.outcome.tokens_out += completion.usage.completion_tokens
        self.outcome.cost += cost
        messages.append(
            ChatMessage(
                "assistant",
                completion.text,
                tool_calls=list(completion.tool_calls),
                reasoning=completion.reasoning,
            )
        )
        self.outcome.records.append(
            Record(
                role="assistant",
                content=completion.text,
                tool_calls=[
                    {"id": c.id, "name": c.name, "arguments": c.arguments}
                    for c in completion.tool_calls
                ],
                reasoning=completion.reasoning,
                model=completion.model,
                usage=completion.usage,
                cost=cost,
                latency_ms=completion.latency_ms,
            )
        )
        return completion

    def _tool_call(
        self, call: ToolCall, by_name: dict[str, Tool]
    ) -> Generator[AgentEvent, None, ToolResult]:
        """Validate and run one tool call, yielding its events; return the ToolResult."""
        self.outcome.tool_calls += 1
        try:
            shown_args: Any = json.loads(call.arguments or "{}")
        except ValueError:
            shown_args = call.arguments[:2000]
        yield AgentEvent("tool_call", {"id": call.id, "name": call.name, "arguments": shown_args})
        result = self._execute(call, by_name)
        yield AgentEvent(
            "tool_result",
            {
                "id": call.id,
                "name": call.name,
                "ok": result.ok,
                "summary": result.summary,
                "evidence": result.evidence,
            },
        )
        if result.chart:
            yield AgentEvent("chart", {"id": call.id, "spec": result.chart})
        hits = find_injection(result.content)
        if hits:
            result.caveats.append(
                {
                    "kind": "instruction_like_data",
                    "message": "The data contains text that looks like instructions; it was "
                    "treated as data and not followed.",
                    "snippets": hits,
                }
            )
        for item in result.caveats:
            yield AgentEvent("caveat", item)
        return result

    def _execute(self, call: ToolCall, by_name: dict[str, Tool]) -> ToolResult:
        tool = by_name.get(call.name)
        if tool is None:
            return ToolResult.error(
                f"Unknown tool '{call.name[:60]}'. Available: {', '.join(sorted(by_name))}.",
                code="unknown_tool",
            )
        try:
            args = parse_arguments(tool, call.arguments)
        except ToolArgumentError as exc:
            return ToolResult.error(exc.message, code="invalid_arguments")
        try:
            return tool.run(self.ctx, args)
        except Exception:
            logger.exception("agent_tool_failed", tool=call.name)
            self.ctx.db.rollback()
            return ToolResult.error("The tool failed unexpectedly.", code="tool_failed")

    def _finish(self, seen_results: list[Any], question: str) -> Iterator[AgentEvent]:
        text = self.outcome.final_text
        untraced = untraced_numbers(text, seen_results, question)
        caveats: list[dict[str, Any]] = []
        if untraced:
            message = caveat_text(untraced)
            caveats.append({"kind": "untraced_numbers", "message": message, "numbers": untraced})
            yield events.caveat("untraced_numbers", message, numbers=untraced)
            text = f"{text}\n\n_{message}_"
        self.outcome.final_text = text
        self.outcome.untraced = untraced
        last = next((r for r in reversed(self.outcome.records) if r.role == "assistant"), None)
        if last is not None:
            last.content = text
            last.caveats = caveats
            last.evidence = {"untraced_numbers": untraced, "tool_calls": self.outcome.tool_calls}
        yield AgentEvent(
            "final",
            {
                "text": text,
                "model": self.model,
                "scripted": self.scripted,
                "untraced_numbers": untraced,
                "tool_calls": self.outcome.tool_calls,
                "usage": {
                    "tokens_in": self.outcome.tokens_in,
                    "tokens_out": self.outcome.tokens_out,
                },
                "cost_usd_approx": float(self.outcome.cost),
                "cost_is_approximate": True,
            },
        )
