"""Data-quality caveats: warnings and confidence caps derived from the Step 3 profile."""

from dataclasses import dataclass, field
from typing import Any

NULL_SHARE_LIMIT_PCT = 5.0
LOW_QUALITY_SCORE = 60
VERY_LOW_QUALITY_SCORE = 40
_RANK = {"low": 0, "medium": 1, "high": 2}


def cap_confidence(confidence: str, cap: str | None) -> str:
    """Return the lower of a confidence and an optional cap (a cap never raises it)."""
    if cap is None or _RANK[confidence] <= _RANK[cap]:
        return confidence
    return cap


@dataclass(frozen=True)
class Caveats:
    """Warnings to attach to an insight and the highest confidence it may keep."""

    warnings: tuple[str, ...] = ()
    cap: str | None = None


@dataclass
class QualityContext:
    """What the profile says about duplicates, nulls and the overall quality score."""

    row_count: int = 0
    duplicate_rows: int = 0
    quality_score: int | None = None
    null_pct: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_version(
        cls, dataset_profile: dict[str, Any] | None, score: int | None, columns: list[Any]
    ) -> "QualityContext":
        """Build the context from a version's stored profile and its column profiles."""
        profile = dataset_profile or {}
        return cls(
            row_count=int(profile.get("row_count") or 0),
            duplicate_rows=int(profile.get("duplicate_rows") or 0),
            quality_score=score,
            null_pct={c.name: float(c.stats.get("null_pct") or 0.0) for c in columns},
        )

    def caveats(self, key_columns: list[str], excluded_rows: int = 0, what: str = "") -> Caveats:
        """Return the warnings and confidence cap for an insight using these key columns.

        `excluded_rows` is how many rows the module dropped because a key column was null.
        """
        warnings: list[str] = []
        cap: str | None = None
        if self.duplicate_rows > 0:
            warnings.append(
                f"{self.duplicate_rows:,} exact duplicate rows are included in these "
                f"{what or 'figures'}."
            )
            cap = "medium"
        heavy = [c for c in key_columns if self.null_pct.get(c, 0.0) > NULL_SHARE_LIMIT_PCT]
        if heavy:
            names = ", ".join(f"'{c}' ({self.null_pct[c]:.1f}% empty)" for c in heavy)
            warnings.append(
                f"{excluded_rows:,} rows with no value in {names} were excluded from this analysis."
            )
            cap = "medium"
        if self.quality_score is not None and self.quality_score < LOW_QUALITY_SCORE:
            warnings.append(
                f"The dataset quality score is low ({self.quality_score}/100), so treat these "
                "results with caution."
            )
            if self.quality_score < VERY_LOW_QUALITY_SCORE:
                cap = "low"
            else:
                cap = cap or "medium"
        return Caveats(tuple(warnings), cap)
