"""Confidence of an answer, computed from the stored results it used, never from the model."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent.loop import Record

LEVELS = ("low", "medium", "high")  # weakest first
EVIDENCE_LISTS = ("insights", "forecasts", "scenarios")


def _rated_items(records: "list[Record]") -> list[dict[str, Any]]:
    """Return every evidence item of the successful tool calls that carries a confidence."""
    items: list[dict[str, Any]] = []
    for record in records:
        if record.role != "tool" or not record.evidence.get("ok"):
            continue
        for key in EVIDENCE_LISTS:
            found = record.evidence.get(key) or []
            items.extend(i for i in found if isinstance(i, dict) and i.get("confidence") in LEVELS)
    return items


def answer_confidence(records: "list[Record]") -> dict[str, Any] | None:
    """Return the weakest confidence behind an answer and its sample size.

    An answer is only as sure as the weakest result it used. Plain query results carry no rating,
    so an answer built only on them has none, rather than an invented one.
    """
    items = _rated_items(records)
    if not items:
        return None
    weakest = min(items, key=lambda item: LEVELS.index(item["confidence"]))
    return {"level": weakest["confidence"], "sample_size": weakest.get("sample_size")}
