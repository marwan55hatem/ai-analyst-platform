"""Tool contract: a Pydantic argument model, a JSON schema for the model, and a typed result."""

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.llm.types import ToolSpec
from app.models.agent import BusinessProfile
from app.models.dataset import Dataset, DatasetVersion


class ToolArgs(BaseModel):
    """Base for tool arguments: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@dataclass
class AgentContext:
    """Everything a tool may use; tenant and dataset come from the conversation, never the model."""

    db: Session
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    dataset: Dataset
    version: DatasetVersion
    profile: BusinessProfile | None
    settings: Settings
    sandbox: Any = None  # SandboxClient; Any keeps tests free to pass a fake

    @property
    def allow_pii(self) -> bool:
        """Return whether the company opted in to personal data in answers."""
        return bool(self.profile and self.profile.allow_pii)


@dataclass
class ToolResult:
    """What a tool returns: `content` for the model, `evidence` for people and the database."""

    ok: bool
    content: dict[str, Any]
    evidence: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    chart: dict[str, Any] | None = None
    caveats: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def error(cls, message: str, *, code: str = "tool_error", **evidence: Any) -> "ToolResult":
        """Return a failed result the model can read and recover from."""
        return cls(
            ok=False,
            content={"error": code, "message": message},
            evidence={"error": code, "message": message, **evidence},
            summary=message,
        )


Runner = Callable[[AgentContext, Any], ToolResult]


@dataclass(frozen=True)
class Tool:
    """One tool: name, description, argument model and the function that runs it."""

    name: str
    description: str
    args_model: type[ToolArgs]
    run: Runner

    def spec(self) -> ToolSpec:
        """Return the spec sent to the model (JSON schema from the argument model)."""
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        schema.setdefault("properties", {})
        schema["additionalProperties"] = False
        return ToolSpec(name=self.name, description=self.description, parameters=schema)


class ToolArgumentError(Exception):
    """The model's arguments are not valid JSON or do not match the schema."""

    def __init__(self, message: str) -> None:
        """Keep a short, model-readable message."""
        super().__init__(message)
        self.message = message


def parse_arguments(tool: Tool, raw: str) -> ToolArgs:
    """Parse and validate raw JSON arguments against the tool's model."""
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ToolArgumentError(f"arguments are not valid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ToolArgumentError("arguments must be a JSON object")
    try:
        return tool.args_model.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}"
            for err in exc.errors()[:5]
        )
        raise ToolArgumentError(f"invalid arguments: {problems}") from exc
