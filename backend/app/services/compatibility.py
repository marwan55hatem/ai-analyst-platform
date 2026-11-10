"""Compare a new dataset version with the previous one before the analysis is trusted.

Added columns never block or warn. A lost date or revenue role, or a file that covers a shorter
period, only warns. A file is blocked only when it cannot be analysed at all.
"""

from collections.abc import Sequence
from typing import Any

from app.schemas.dataset import ColumnChange, CompatibilityOut

REQUIRED_ROLES = ("date", "revenue")
MIN_ROW_SHARE = 0.5
NOT_ANALYSABLE = (
    "The file has no date column and no revenue or quantity column, so nothing can be analysed."
)


def _roles(profiles: Sequence[Any]) -> set[str]:
    return {p.semantic_role for p in profiles}


def _has_money(roles: set[str]) -> bool:
    return "revenue" in roles or "quantity" in roles or {"quantity", "price"} <= roles


def is_analysable(profiles: Sequence[Any]) -> bool:
    """Return True when the file has a date column and a revenue or quantity column."""
    roles = _roles(profiles)
    return "date" in roles and _has_money(roles)


def _first_date(profiles: Sequence[Any]) -> str | None:
    dates = [p.stats.get("min") for p in profiles if p.semantic_role == "date" and p.stats]
    values = sorted(str(d) for d in dates if d)
    return values[0] if values else None


def compare(
    version: int,
    previous_version: int | None,
    prev: Sequence[Any],
    new: Sequence[Any],
    *,
    previous_rows: int | None,
    new_rows: int | None,
) -> CompatibilityOut:
    """Compare two sets of column profiles (needs `name`, `inferred_type`, `semantic_role`)."""
    blocking = not is_analysable(new)
    if previous_version is None:
        return CompatibilityOut(
            version=version,
            previous_version=None,
            status="blocked" if blocking else "initial",
            blocking=blocking,
            blocking_reason=NOT_ANALYSABLE if blocking else None,
            new_rows=new_rows,
        )
    before = {p.name: p for p in prev}
    after = {p.name: p for p in new}
    changed = [
        ColumnChange(
            name=name,
            previous_type=before[name].inferred_type,
            new_type=after[name].inferred_type,
            previous_role=before[name].semantic_role,
            new_role=after[name].semantic_role,
        )
        for name in after
        if name in before
        and (
            before[name].inferred_type != after[name].inferred_type
            or before[name].semantic_role != after[name].semantic_role
        )
    ]
    lost = [r for r in REQUIRED_ROLES if r in _roles(prev) and r not in _roles(new)]
    old_first, new_first = _first_date(prev), _first_date(new)
    later_start = bool(old_first and new_first and new_first > old_first)
    few_rows = bool(
        previous_rows and new_rows is not None and new_rows < MIN_ROW_SHARE * previous_rows
    )
    shorter = later_start or few_rows
    missing = sorted(set(before) - set(after))
    warn = bool(missing or changed or lost or shorter)
    return CompatibilityOut(
        version=version,
        previous_version=previous_version,
        status="blocked" if blocking else ("warnings" if warn else "compatible"),
        added=sorted(set(after) - set(before)),
        missing=missing,
        changed=changed,
        missing_roles=lost,
        shorter_period=shorter,
        previous_rows=previous_rows,
        new_rows=new_rows,
        previous_first_date=old_first,
        new_first_date=new_first,
        blocking=blocking,
        blocking_reason=NOT_ANALYSABLE if blocking else None,
    )
