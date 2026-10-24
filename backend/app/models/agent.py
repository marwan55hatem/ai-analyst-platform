"""Agent data: company profile, conversations, messages and the daily token usage."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin

MESSAGE_ROLES = ("user", "assistant", "tool")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class BusinessProfile(TenantMixin, Base):
    """What the agent should know about the company; one row per tenant."""

    __tablename__ = "business_profiles"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    industry: Mapped[str] = mapped_column(String(100), default="")
    goals: Mapped[str] = mapped_column(Text, default="")
    currency: Mapped[str] = mapped_column(String(3), default="")
    language: Mapped[str] = mapped_column(String(2), default="en")
    constraints: Mapped[str] = mapped_column(Text, default="")
    allow_pii: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Conversation(TenantMixin, Base):
    """A chat thread about one dataset."""

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_tenant_updated", "tenant_id", "updated_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    summary: Mapped[str] = mapped_column(Text, default="")
    summarized_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Message(TenantMixin, Base):
    """One message: the user's question, a model turn or a tool result, with accounting."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(f"role in ({_in(MESSAGE_ROLES)})", name="role"),
        Index("ix_messages_conversation_seq", "conversation_id", "seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    caveats: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # Model reasoning is replayed to the provider (DeepSeek thinking mode) but never returned.
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    tokens_cached: Mapped[int] = mapped_column(Integer, default=0)
    tokens_reasoning: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd_approx: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal(0))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentUsageDaily(TenantMixin, Base):
    """Tokens used (and reserved) by a tenant on one UTC day, for the daily budget."""

    __tablename__ = "agent_usage_daily"
    __table_args__ = (UniqueConstraint("tenant_id", "day", name="uq_agent_usage_daily_tenant_day"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    day: Mapped[date] = mapped_column(Date)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
