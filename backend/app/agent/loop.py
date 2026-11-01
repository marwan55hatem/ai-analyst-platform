"""The agent loop: model turns, validated tool calls, verified numbers, typed events.

One question runs at most `AGENT_MAX_ITERATIONS` model turns with tools; if the model still wants
tools after that, a last turn without tools asks for an answer from the results so far. Every
model turn is paid for out of the tenant's daily budget, reserved before the call.
"""

import json
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import structlog

from app.agent import events
from app.agent.budget import BudgetExceededError, TokenBudget
from app.agent.confidence import answer_confidence
from app.agent.context import is_replayable, prompt_estimate, shrink_tool_results
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
from app.agent.verifier import caveat_text, count_checked_numbers, untraced_numbers
from app.core.config import Settings
from app.llm.base import LLMClient, LLMError, LLMRequestTooLargeError, LLMToolCallError
from app.llm.pricing import approximate_cost_usd
from app.llm.types import (
    ChatMessage,
    Completion,
    RateLimitWait,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
    estimate_tokens,
)

logger = structlog.get_logger()

FINAL_ROUND_NOTE = (
    "You have used all tool calls for this question. Answer now from the tool results above, "
    "or say what could not be determined. Do not request more tools."
)
EMPTY_ANSWER = "The model returned no answer. Please rephrase the question."
OUT_OF_TOKENS_ANSWER = (
    "The model ran out of output tokens before it could answer. Please ask something narrower."
)
TRUNCATED_CAVEAT = "This answer was cut off at the model's output limit, so it may be incomplete."
REPAIR_NOTE = (
    "Your last tool call was invalid. Call one of the listed tools by its exact name with valid "
    "JSON arguments, or answer without a tool."
)
BAD_CALL_NOTE = "A tool call was dropped: '{name}' is not usable. Valid tools: {tools}."
OLDER_RESULT_CHARS = 1_500
RETRY_RESULT_CHARS = 1_000


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
    truncated: bool = False


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
        self._repaired = False
        self._trimmed = False
        self._shown = ""  # text of the model turn in progress, already sent to the user

    def run(
        self, system: str, history: list[ChatMessage], question: str
    ) -> Generator[AgentEvent, None, None]:
        """Yield the events of one question; `self.outcome` holds what to persist."""
        messages = [ChatMessage("system", system), *history, ChatMessage("user", question)]
        specs = [tool.spec() for tool in self.tools]
        by_name = {tool.name: tool for tool in self.tools}
        seen_results: list[Any] = []
        limit = self.settings.agent_max_iterations
        bad_rounds = 0
        for iteration in range(limit + 1):
            last_round = iteration == limit
            if last_round:
                messages.append(ChatMessage("system", FINAL_ROUND_NOTE))
            completion = yield from self._model_turn(messages, [] if last_round else specs)
            if completion is None:
                return
            if not completion.tool_calls or last_round:
                self.outcome.final_text = _answer_text(completion)
                break
            bad_calls = [c for c in completion.tool_calls if not is_replayable(c, by_name)]
            bad_rounds = bad_rounds + 1 if bad_calls else 0
            if bad_rounds >= 2:  # the tool error of the first bad round was the repair message
                yield from self._fail(LLMToolCallError())
                return
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
                        evidence={
                            "ok": result.ok,
                            "summary": result.summary,
                            **result.evidence,
                            **({"chart": result.chart} if result.chart else {}),
                        },
                        caveats=result.caveats,
                    )
                )
            _forget_bad_calls(messages, bad_calls, sorted(by_name))
        yield from self._finish(seen_results, question)

    def close_turn(self, reason: Literal["stopped", "failed"]) -> None:
        """Store what the user saw of a turn that never reached its final answer.

        The record is marked `partial`: its numbers were never verified and it is not replayed to
        the model. A failure that showed nothing leaves no record, so the question stays open.
        """
        if self._shown or reason == "stopped":
            self.outcome.records.append(
                Record(
                    role="assistant",
                    content=self._shown,
                    evidence={"partial": reason, "tool_calls": self.outcome.tool_calls},
                    model=self.model,
                )
            )

    def _fit_prompt(self, messages: list[ChatMessage], specs: list[ToolSpec]) -> None:
        """Trim older tool results, then all of them, when the prompt is over the limit."""
        limit = self.settings.llm_max_prompt_tokens
        if not limit or prompt_estimate(messages, specs) <= limit:
            return
        shrink_tool_results(messages, OLDER_RESULT_CHARS, spare_last=_latest_results(messages))
        if prompt_estimate(messages, specs) > limit:
            shrink_tool_results(messages, OLDER_RESULT_CHARS)
        logger.info("agent_prompt_trimmed", tokens=prompt_estimate(messages, specs), limit=limit)

    def _model_turn(
        self, messages: list[ChatMessage], specs: list[ToolSpec]
    ) -> Generator[AgentEvent, None, Completion | None]:
        """Reserve budget, stream one model turn, record it; return the Completion or None.

        A rejected tool call or an oversized prompt gets one retry, unless answer text has already
        reached the user: that text cannot be taken back.
        """
        self._fit_prompt(messages, specs)
        logger.info("agent_prompt_size", tokens=prompt_estimate(messages, specs))
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
        shown = False
        stream = self.llm.chat(messages, specs, model=self.model)
        try:
            for event in stream:
                if isinstance(event, TextDelta):
                    if event.text:
                        shown = True
                        self._shown += event.text
                        yield events.token(event.text)
                elif isinstance(event, RateLimitWait):
                    yield events.rate_limited(event.retry_in_s)
                else:
                    completion = event
        except LLMToolCallError as exc:
            self.budget.settle(reserved, 0)
            if shown or self._repaired:
                yield from self._fail(exc)
                return None
            self._repaired = True
            logger.warning("agent_tool_call_repair", code=exc.code)
            note = ChatMessage("system", REPAIR_NOTE)
            messages.append(note)
            repaired = yield from self._model_turn(messages, specs)
            messages.remove(note)
            return repaired
        except LLMRequestTooLargeError as exc:
            self.budget.settle(reserved, 0)
            if shown or self._trimmed:
                yield from self._fail(exc)
                return None
            self._trimmed = True
            shrink_tool_results(messages, RETRY_RESULT_CHARS)
            return (yield from self._model_turn(messages, specs))
        except LLMError as exc:
            self.budget.settle(reserved, 0)
            yield from self._fail(exc)
            return None
        except GeneratorExit:  # the client left; pay for the prompt and the text it received
            sent = estimate - self.settings.llm_max_output_tokens
            self.budget.settle(reserved, sent + estimate_tokens(self._shown))
            raise
        except Exception:
            self.budget.settle(reserved, 0)
            raise
        finally:
            getattr(stream, "close", lambda: None)()
        if completion is None:
            self.budget.settle(reserved, 0)
            self.outcome.error = "provider_error"
            yield events.error("provider_error", "The model stream ended without a result.")
            return None
        self._shown = ""
        # A provider that reports no usage is charged the reservation, never nothing.
        self.budget.settle(reserved, completion.usage.total_tokens or reserved)
        self.outcome.truncated = completion.finish_reason == "length" and bool(completion.text)
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

    def _fail(self, exc: LLMError) -> Iterator[AgentEvent]:
        """Record a provider failure and emit it as the terminal error event."""
        self.outcome.error = exc.code
        logger.warning("agent_llm_error", code=exc.code)
        retry_after = getattr(exc, "retry_after_s", None)
        extra = {} if retry_after is None else {"retry_after_s": retry_after}
        yield events.error(exc.code, exc.user_message, **extra)

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
        trust = {
            "untraced_numbers": untraced,
            "numbers_checked": count_checked_numbers(text, question),
            "scripted": self.scripted,
            "confidence": answer_confidence(self.outcome.records),
            "tool_calls": self.outcome.tool_calls,
        }
        caveats: list[dict[str, Any]] = []
        if untraced:
            message = caveat_text(untraced)
            caveats.append({"kind": "untraced_numbers", "message": message, "numbers": untraced})
            yield events.caveat("untraced_numbers", message, numbers=untraced)
            text = f"{text}\n\n_{message}_"
        if self.outcome.truncated:
            caveats.append({"kind": "answer_truncated", "message": TRUNCATED_CAVEAT})
            yield events.caveat("answer_truncated", TRUNCATED_CAVEAT)
            text = f"{text}\n\n_{TRUNCATED_CAVEAT}_"
        self.outcome.final_text = text
        self.outcome.untraced = untraced
        last = next((r for r in reversed(self.outcome.records) if r.role == "assistant"), None)
        if last is not None:
            last.content = text
            last.caveats = caveats
            last.evidence = trust
        yield AgentEvent(
            "final",
            {
                "text": text,
                "model": self.model,
                **trust,
                "usage": {
                    "tokens_in": self.outcome.tokens_in,
                    "tokens_out": self.outcome.tokens_out,
                },
                "cost_usd_approx": float(self.outcome.cost),
                "cost_is_approximate": True,
            },
        )


def _answer_text(completion: Completion) -> str:
    """Return the answer; reasoning is never used in its place."""
    text = completion.text.strip()
    if text:
        return text
    return OUT_OF_TOKENS_ANSWER if completion.finish_reason == "length" else EMPTY_ANSWER


def _forget_bad_calls(
    messages: list[ChatMessage], bad_calls: list[ToolCall], tool_names: list[str]
) -> None:
    """Take bad calls out of what the provider sees and say which tools exist instead.

    Stored records and events keep them; only the prompt changes (see `is_replayable`).
    """
    if not bad_calls:
        return
    ids = {c.id for c in bad_calls}
    assistant = next(m for m in reversed(messages) if m.role == "assistant")
    assistant.tool_calls = [c for c in assistant.tool_calls if c.id not in ids]
    if not assistant.tool_calls and not assistant.content:
        messages.remove(assistant)
    messages[:] = [m for m in messages if m.tool_call_id not in ids]
    for call in bad_calls:
        note = BAD_CALL_NOTE.format(name=call.name[:60], tools=", ".join(tool_names))
        messages.append(ChatMessage("system", note))


def _latest_results(messages: list[ChatMessage]) -> int:
    """Return how many tool results follow the last assistant message."""
    count = 0
    for message in reversed(messages):
        if message.role != "tool":
            break
        count += 1
    return count
