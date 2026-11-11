"""Tool contract: a Pydantic argument model, a JSON schema for the model, and a typed result."""

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.pseudonyms import PseudonymMap, load_pseudonym_map
from app.llm.types import ToolSpec
from app.models.agent import BusinessProfile
from app.models.dataset import Dataset, DatasetVersion

ENVELOPE_CHARS = 400  # room for the delimiters around a result and a note


def content_size(content: Any) -> int:
    """Return the characters a tool result takes once serialised for the model."""
    return len(json.dumps(content, ensure_ascii=False, default=str))


def fit_rows(content: dict[str, Any], rows: list[Any], limit: int) -> str | None:
    """Drop whole rows from the end of `rows`, a list inside `content`, until `content` fits.

    A cut in the middle of a row invites the model to complete it: a live run invented a customer's
    figures that way. Returns a note for the model when rows were dropped.
    """
    total = len(rows)
    while len(rows) > 1 and content_size(content) > limit - ENVELOPE_CHARS:
        rows.pop()
    if len(rows) == total:
        return None
    return (
        f"Only the first {len(rows)} of {total} rows fit here. Tell the user the list is partial; "
        "never guess the rest. Ask for fewer rows or a narrower filter to see others."
    )


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
    _pseudonyms: Any = field(default=None, repr=False)
    _pseudonyms_loaded: bool = field(default=False, repr=False)

    def pseudonyms(self) -> "PseudonymMap | None":
        """Return this version's customer-code map (built once per turn; None: no customer id)."""
        if not self._pseudonyms_loaded:
            self._pseudonyms = load_pseudonym_map(
                self.db, self.tenant_id, self.version, self.settings
            )
            self._pseudonyms_loaded = True
        mapping: PseudonymMap | None = self._pseudonyms
        return mapping

    def display_map(self) -> "PseudonymMap | None":
        """Return the map people see through (real ids, or the pages' pseudonyms if hidden)."""
        from app.agent.demask import display_map_for

        if self.pseudonyms() is None:
            return None
        return display_map_for(
            self.db, self.tenant_id, self.version, self.settings, allow_pii=self.allow_pii
        )

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
        full = self.args_model.model_json_schema()
        schema: dict[str, Any] = plain_schema(full, full.get("$defs", {}))
        schema.setdefault("properties", {})
        schema["additionalProperties"] = False
        return ToolSpec(name=self.name, description=self.description, parameters=schema)


def plain_schema(node: Any, defs: dict[str, Any]) -> Any:
    """Inline `$ref`s and drop generated titles from a JSON schema.

    A live run on Groq (Step 8c) sent `what_if` scenarios without their required `lever` and
    `change_pct`: they sat behind `$ref: #/$defs/ScenarioIn`. Inlined, every field is visible where
    it is used. Only schema annotations named `title` go; a property called `title` stays.
    """
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        merged = {
            **defs[ref.removeprefix("#/$defs/")],
            **{k: v for k, v in node.items() if k != "$ref"},
        }
        return plain_schema(merged, defs)
    out = {k: v for k, v in node.items() if k not in ("$defs", "title")}
    if isinstance(out.get("properties"), dict):
        out["properties"] = {
            name: plain_schema(sub, defs) for name, sub in out["properties"].items()
        }
    if "items" in out:
        out["items"] = plain_schema(out["items"], defs)
    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(out.get(key), list):
            out[key] = [plain_schema(sub, defs) for sub in out[key]]
    return out


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
