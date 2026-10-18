"""Module framework: typed results, insights, the context modules receive, and the base class."""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.config import Settings
from app.ml.engine import AnalysisConnection
from app.ml.quality import QualityContext
from app.ml.roles import RoleMap

Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class ModuleResult:
    """The convention every ML result follows: value, confidence, sample_size, method, warnings."""

    value: Any
    confidence: Confidence
    sample_size: int
    method: str
    warnings: tuple[str, ...] = ()


@dataclass
class Insight:
    """A finding ready to be stored: text, ranking, chart and the evidence behind it."""

    module: str
    type: str
    title: str
    summary: str
    result: ModuleResult
    impact_score: float = 0.0
    money_effect: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)
    chart_spec: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def stored_payload(self) -> dict[str, Any]:
        """Return the payload with the result fields (value, method, warnings) merged in."""
        return {
            "value": self.result.value,
            "method": self.result.method,
            "warnings": list(self.result.warnings),
            "money_effect": self.money_effect,
            **self.payload,
        }


@dataclass
class AnalysisContext:
    """Everything a module may use; it never touches the database or the file itself."""

    tenant_id: uuid.UUID
    version_id: uuid.UUID
    roles: RoleMap
    db: AnalysisConnection
    settings: Settings
    quality: QualityContext
    config: dict[str, Any]
    granularity: str | None = None
    warnings: list[str] = field(default_factory=list)
    cache: dict[str, Any] = field(default_factory=dict)

    def warn(self, message: str) -> None:
        """Record a module-level warning (stored on the run, shown next to the module)."""
        self.warnings.append(message)

    @property
    def currency(self) -> str | None:
        """Currency code chosen for this run, if any."""
        value = self.config.get("currency")
        return str(value) if value else None

    @property
    def locale(self) -> str:
        """Locale used to format numbers in insight text."""
        return str(self.config.get("locale") or "en")


class AnalysisModule(ABC):
    """One analysis. `requires` lists the roles that must exist for it to run."""

    name: str
    title: str
    requires: frozenset[str]
    # at least one of these roles must exist as well (empty means no such condition)
    requires_any: frozenset[str] = frozenset()

    @abstractmethod
    def run(self, context: AnalysisContext) -> list[Insight]:
        """Return the insights, raising only for unexpected failures."""

    def notes(self, roles: RoleMap) -> list[str]:
        """Return plain-language notes about parts of this module that will be skipped."""
        return []
