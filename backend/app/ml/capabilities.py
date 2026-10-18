"""Capability detection: which modules can run for a role map, and why the others cannot."""

from dataclasses import dataclass, field

from app.ml.base import AnalysisModule
from app.ml.roles import RoleMap

_MISSING_PHRASE = {
    "revenue": "no revenue column (and no quantity and price to derive it from)",
    "date": "no date column",
    "customer_id": "no customer_id column",
    "quantity": "no quantity column",
    "price": "no price column",
}


@dataclass
class Capability:
    """Whether one module can run, with a plain-language reason when it cannot."""

    name: str
    title: str
    available: bool
    reason: str | None = None
    notes: list[str] = field(default_factory=list)


def check(module: AnalysisModule, roles: RoleMap) -> Capability:
    """Decide whether a module can run with this role map."""
    missing = [r for r in sorted(module.requires) if not roles.has(r)]
    if missing:
        phrases = " and ".join(_MISSING_PHRASE.get(r, f"no {r} column") for r in missing)
        return Capability(
            module.name, module.title, False, f"{phrases}, so {module.title.lower()} is skipped"
        )
    if module.requires_any and not any(roles.has(r) for r in module.requires_any):
        options = " or ".join(sorted(module.requires_any))
        return Capability(
            module.name,
            module.title,
            False,
            f"no {options} column, so {module.title.lower()} is skipped",
        )
    return Capability(module.name, module.title, True, None, module.notes(roles))


def detect(modules: list[AnalysisModule], roles: RoleMap) -> list[Capability]:
    """Return the capability of every module."""
    return [check(m, roles) for m in modules]
