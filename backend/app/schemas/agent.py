"""Requests and responses of the company profile, conversations and chat endpoints."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_QUESTION_CHARS = 4_000


class BusinessProfileIn(BaseModel):
    """What the agent should know about the company (all fields optional)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    industry: str = Field(default="", max_length=100)
    goals: str = Field(default="", max_length=2_000)
    currency: str = Field(default="", max_length=3, pattern=r"^([A-Za-z]{3})?$")
    language: Literal["en", "ar"] = "en"
    constraints: str = Field(default="", max_length=2_000)
    allow_pii: bool = Field(
        default=False,
        description="Allow personal-data columns in agent answers (default off).",
    )


class BusinessProfileOut(BusinessProfileIn):
    """The stored profile."""

    model_config = ConfigDict(from_attributes=True)

    updated_at: datetime | None = None


class ConversationCreate(BaseModel):
    """Start a conversation about one dataset."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dataset_id: uuid.UUID
    title: str | None = Field(default=None, max_length=200)


class ConversationRename(BaseModel):
    """New title of a conversation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)


class ConversationOut(BaseModel):
    """A conversation without its messages."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    user_id: uuid.UUID
    title: str
    summary: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    """A stored message; model reasoning is never returned."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    role: str
    content: str
    tool_calls: list[dict[str, Any]]
    tool_call_id: str | None
    tool_name: str | None
    evidence: dict[str, Any]
    caveats: list[dict[str, Any]]
    model: str | None
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    tokens_reasoning: int
    cost_usd_approx: float = Field(description="Approximate cost in USD (prices are estimates).")
    latency_ms: int
    created_at: datetime


class ChatRequest(BaseModel):
    """One question in a conversation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    conversation_id: uuid.UUID
    message: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
