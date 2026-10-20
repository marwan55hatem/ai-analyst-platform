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


@dataclass(frozen=True)
class DataFacts:
    """What the stored profile already knows about a version (nothing is recomputed).

    Modules use it to say up front, in plain words, that the data is too small for them.
    """

    row_count: int = 0
    distinct: dict[str, int] = field(default_factory=dict)  # role -> distinct values
    date_span_days: int | None = None
    granularity: str | None = None
    pii_roles: frozenset[str] = frozenset()  # roles whose column is flagged as personal data

    @classmethod
    def from_profiles(
        cls, roles: RoleMap, profiles: list[Any], dataset_profile: dict[str, Any] | None
    ) -> "DataFacts":
        """Build the facts for a role map from the version's column profiles."""
        by_name = {p.name: p for p in profiles}
        distinct: dict[str, int] = {}
        pii: set[str] = set()
        for role, name in roles.columns.items():
            profile = by_name.get(name)
            if profile is None:
                continue
            count = profile.stats.get("distinct_count")
            if count is not None:
                distinct[role] = int(count)
            if getattr(profile, "pii_flag", False):
                pii.add(role)
        date_profile = by_name.get(roles.columns.get("date", ""))
        stats = date_profile.stats if date_profile is not None else {}
        span = stats.get("span_days")
        return cls(
            row_count=int((dataset_profile or {}).get("row_count") or 0),
            distinct=distinct,
            date_span_days=None if span is None else int(span),
            granularity=stats.get("granularity"),
            pii_roles=frozenset(pii),
        )


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
    facts: DataFacts = field(default_factory=DataFacts)
    version_number: int | None = None
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

    def data_requirement(self, facts: DataFacts) -> str | None:
        """Return why the data is too small for this module (plain words), or None if it fits."""
        return None
