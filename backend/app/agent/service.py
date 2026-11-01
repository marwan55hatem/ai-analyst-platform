"""Chat orchestration: load the conversation, run the agent, stream events, persist everything."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache

import structlog
from sqlalchemy.orm import Session

from app.agent import events
from app.agent.budget import TokenBudget
from app.agent.context import is_replayable
from app.agent.events import AgentEvent, jsonable
from app.agent.loop import AgentLoop, Record
from app.agent.prompt import build_system_prompt
from app.agent.sandbox_client import SandboxClient
from app.agent.tools import TOOLS_BY_NAME, AgentContext
from app.core.config import Settings, get_settings
from app.core.errors import AppError, ForbiddenError
from app.db.repository import (
    BusinessProfileRepository,
    ConversationRepository,
    DatasetRepository,
    MessageRepository,
)
from app.db.session import get_sessionmaker
from app.llm import LLMError, ScriptedAnalyst, get_llm_client
from app.llm.base import LLMClient
from app.llm.router import choose_model
from app.llm.scripted import SUMMARY_MARKER
from app.llm.types import ChatMessage, Completion, ToolCall
from app.models.agent import Conversation, Message
from app.models.user import Role, User
from app.services.datasets import current_ready_version

logger = structlog.get_logger()
TITLE_CHARS = 80
SUMMARY_INSTRUCTION = (
    f"{SUMMARY_MARKER}\nSummarise the conversation below in at most "
    "8 short bullet points: the questions asked, the conclusions and open questions. Do not add "
    "numbers that are not in the messages."
)


@lru_cache
def _sandbox(url: str, timeout_s: float) -> SandboxClient:
    return SandboxClient(url, timeout_s)


def can_manage(user: User, conversation: Conversation) -> bool:
    """Return whether the user is the creator, an owner or an admin."""
    return conversation.user_id == user.id or user.role in (Role.owner, Role.admin)


def require_manage(user: User, conversation: Conversation) -> None:
    """Raise 403 unless the user may change the conversation."""
    if not can_manage(user, conversation):
        raise ForbiddenError("Only the creator, an owner or an admin can change this conversation")


def history_messages(rows: list[Message]) -> list[ChatMessage]:
    """Rebuild provider messages from stored rows, starting at a user message.

    A window that started with a tool result or an assistant tool call would be rejected by the
    provider, so leading rows before the first user message are dropped. Bad tool calls are dropped
    with their results: while one stays in the history Groq refuses every later request, which
    would break the conversation for good.
    """
    first_user = next((i for i, row in enumerate(rows) if row.role == "user"), len(rows))
    result: list[ChatMessage] = []
    dropped: set[str | None] = set()
    for row in rows[first_user:]:
        if row.role == "assistant":
            calls = [ToolCall(c["id"], c["name"], c["arguments"]) for c in row.tool_calls or []]
            kept = [c for c in calls if is_replayable(c, TOOLS_BY_NAME)]
            dropped.update(c.id for c in calls if c not in kept)
            if calls and not kept and not row.content:
                continue
            result.append(
                ChatMessage("assistant", row.content, tool_calls=kept, reasoning=row.reasoning)
            )
        elif row.role == "tool":
            if row.tool_call_id in dropped:
                continue
            result.append(ChatMessage("tool", row.content, tool_call_id=row.tool_call_id))
        else:
            result.append(ChatMessage("user", row.content))
    return result


class ChatSession:
    """One streamed question; create it inside the request, iterate `events()` in the response."""

    def __init__(
        self,
        user: User,
        conversation_id: uuid.UUID,
        question: str,
        *,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        sandbox: object | None = None,
        session_factory: object | None = None,
    ) -> None:
        """Keep ids only; the stream opens its own database session."""
        self.tenant_id = user.tenant_id
        self.user_id = user.id
        self.conversation_id = conversation_id
        self.question = question
        self.settings = settings or get_settings()
        self._llm = llm
        self._sandbox = sandbox
        self._factory = session_factory or get_sessionmaker()

    def frames(self) -> Iterator[str]:
        """Yield SSE frames."""
        for event in self.events():
            yield event.sse()

    def events(self) -> Iterator[AgentEvent]:
        """Run the question and yield its events; everything is stored even when it fails."""
        with self._factory() as db:  # type: ignore[operator]
            yield from self._run(db)

    def _run(self, db: Session) -> Iterator[AgentEvent]:
        conversations = ConversationRepository(db, self.tenant_id)
        conversation = conversations.get(self.conversation_id)
        if conversation is None:
            yield events.error("not_found", "Conversation not found")
            return
        messages = MessageRepository(db, self.tenant_id)
        prior = messages.for_conversation(conversation.id)
        seq = messages.next_seq(conversation.id)
        messages.add(
            conversation_id=conversation.id,
            seq=seq,
            role="user",
            content=self.question,
            tool_calls=[],
            evidence={},
            caveats=[],
        )
        if not prior:
            conversation.title = self.question[:TITLE_CHARS]
        db.commit()
        try:
            llm = self._llm or get_llm_client()
        except LLMError as exc:
            yield events.error(exc.code, exc.user_message)
            return
        if llm is None:
            yield events.error(
                "llm_not_configured", "No model provider is configured (LLM_PROVIDER=none)."
            )
            return
        try:
            dataset = DatasetRepository(db, self.tenant_id).get_or_404(conversation.dataset_id)
            version = current_ready_version(db, dataset)
        except AppError as exc:
            yield events.error(exc.code, exc.message)
            return
        profile = BusinessProfileRepository(db, self.tenant_id).current()
        budget = TokenBudget(self._factory, self.tenant_id, self.settings.agent_daily_token_budget)
        self._maybe_summarise(db, conversation, prior, llm, budget)
        window = prior[-self.settings.agent_history_messages :] if prior else []
        ctx = AgentContext(
            db=db,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            dataset=dataset,
            version=version,
            profile=profile,
            settings=self.settings,
            sandbox=self._sandbox
            if self._sandbox is not None
            else _sandbox(self.settings.sandbox_url, self.settings.sandbox_timeout_s),
        )
        loop = AgentLoop(
            llm,
            ctx,
            budget,
            self.settings,
            model=choose_model(self.question, self.settings),
            scripted=isinstance(llm, ScriptedAnalyst),
        )
        system = build_system_prompt(profile, dataset, conversation.summary)
        final: AgentEvent | None = None
        failure: AgentEvent | None = None
        try:
            for event in loop.run(system, history_messages(window), self.question):
                if event.type == "final":
                    final = event
                    continue
                yield event
        except Exception:
            logger.exception("agent_turn_failed")
            failure = events.error("internal_error", "The answer failed unexpectedly; try again.")
        finally:
            db.rollback()  # drop anything a failed tool left half-done before persisting
            last_id = self._persist(db, conversation, loop.outcome.records, seq + 1)
        if failure is not None:
            yield failure
        elif final is not None:
            yield AgentEvent(
                "final", {**final.data, "message_id": str(last_id) if last_id else None}
            )

    def _persist(
        self, db: Session, conversation: Conversation, records: list[Record], seq: int
    ) -> uuid.UUID | None:
        repo = MessageRepository(db, self.tenant_id)
        last: uuid.UUID | None = None
        for offset, record in enumerate(records):
            row = repo.add(
                id=uuid.uuid4(),  # set here so the final event can name the stored message
                conversation_id=conversation.id,
                seq=seq + offset,
                role=record.role,
                content=record.content,
                tool_calls=record.tool_calls,
                tool_call_id=record.tool_call_id,
                tool_name=record.tool_name,
                evidence=jsonable(record.evidence),
                caveats=jsonable(record.caveats),
                reasoning=record.reasoning,
                model=record.model,
                tokens_in=record.usage.prompt_tokens,
                tokens_out=record.usage.completion_tokens,
                tokens_cached=record.usage.cached_tokens,
                tokens_reasoning=record.usage.reasoning_tokens,
                cost_usd_approx=record.cost,
                latency_ms=record.latency_ms,
            )
            if record.role == "assistant":
                last = row.id
        conversation.updated_at = datetime.now(UTC)
        db.commit()
        return last

    def _maybe_summarise(
        self,
        db: Session,
        conversation: Conversation,
        prior: list[Message],
        llm: LLMClient,
        budget: TokenBudget,
    ) -> None:
        """Fold messages older than the history window into the conversation summary."""
        keep = self.settings.agent_history_messages
        old = prior[:-keep] if len(prior) > keep else []
        if (
            len(prior) < self.settings.agent_summary_after
            or len(old) <= conversation.summarized_count
        ):
            return
        lines = [f"{m.role}: {m.content[:500]}" for m in old if m.role in ("user", "assistant")]
        messages = [
            ChatMessage("system", SUMMARY_INSTRUCTION),
            *[ChatMessage("user", m.content[:500]) for m in old if m.role == "user"],
            ChatMessage("user", "\n".join(lines)[-12_000:]),
        ]
        try:
            reserved = budget.reserve(sum(len(m.content) for m in messages) // 4 + 600)
        except Exception:  # noqa: BLE001 - no budget: keep the old summary, the answer still runs
            return
        text, used = "", 0
        try:
            for event in llm.chat(messages, [], model=choose_model("", self.settings, "summary")):
                if isinstance(event, Completion):
                    text, used = event.text, event.usage.total_tokens
        except LLMError:
            logger.warning("conversation_summary_failed")
        finally:
            budget.settle(reserved, used)
        if text:
            conversation.summary = text[:4_000]
            conversation.summarized_count = len(old)
            db.commit()


def message_cost(message: Message) -> float:
    """Return the stored approximate cost as a float."""
    return float(message.cost_usd_approx or Decimal(0))
