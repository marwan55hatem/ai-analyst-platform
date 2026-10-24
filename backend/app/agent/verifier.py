"""Number verifier: every number in the final answer must appear in this turn's tool results.

What it proves: a number in the answer matches some number in the tool results (allowing rounding
to the precision shown, thousands separators, k/M/B suffixes, percent and fraction forms, and
Arabic-Indic digits). What it does not prove: that the number is used for the right quantity. That
is why evidence is always shown and the Step 9 evaluation checks answers against ground truth.
"""

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

_ARABIC_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩"  # Arabic-Indic
    "۰۱۲۳۴۵۶۷۸۹"  # Persian
    "٫٬٪",  # decimal and thousands separators, percent sign
    "01234567890123456789.,%",
)
# A number with optional sign, thousands separators, decimals, suffix and percent sign.
_NUMBER = re.compile(
    r"(?<![\w.])([-+\u2212]?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*"
    r"(k|K|thousand|m|M|mn|million|bn|B|billion)?\b\s*(%|percent\b|per cent\b)?"
)
_SUFFIX = {
    "k": 1e3,
    "thousand": 1e3,
    "m": 1e6,
    "mn": 1e6,
    "million": 1e6,
    "bn": 1e9,
    "b": 1e9,
    "billion": 1e9,
}
EXEMPT_MAX = 12  # small integers: list numbers, months, "top 5", "3 scenarios"
MAX_SOURCE_NUMBERS = 50_000
_CAVEAT_TAIL = re.compile(r"\n\n_Caveat: .*$", re.DOTALL)


@dataclass(frozen=True)
class FoundNumber:
    """A number as written in the answer and its value."""

    text: str
    value: float
    decimals: int
    percent: bool
    scale: float = 1.0


def extract_numbers(text: str) -> list[FoundNumber]:
    """Return the numbers written in `text`, skipping ISO dates, times and version-like tokens."""
    normalised = text.translate(_ARABIC_DIGITS)
    normalised = re.sub(r"\b\d{4}-\d{2}(-\d{2})?\b", " ", normalised)  # dates
    normalised = re.sub(r"\b\d{1,2}:\d{2}(:\d{2})?\b", " ", normalised)  # times
    found: list[FoundNumber] = []
    for match in _NUMBER.finditer(normalised):
        sign, whole, fraction, suffix, percent = match.groups()
        digits = whole.replace(",", "") + (fraction or "")
        try:
            value = float(Decimal(digits))
        except InvalidOperation:
            continue
        scale = _SUFFIX[suffix.lower()] if suffix else 1.0
        value *= scale
        if sign in ("-", "\u2212"):
            value = -value
        decimals = len(fraction) - 1 if fraction else 0
        found.append(FoundNumber(match.group(0).strip(), value, decimals, bool(percent), scale))
    return found


def source_numbers(value: Any) -> list[float]:
    """Collect every number in tool results: numeric leaves and numbers written in strings."""
    numbers: list[float] = []

    def walk(item: Any, depth: int) -> None:
        if depth > 30 or len(numbers) > MAX_SOURCE_NUMBERS:
            return
        if isinstance(item, bool) or item is None:
            return
        if isinstance(item, (int, float)):
            if math.isfinite(float(item)):
                numbers.append(float(item))
        elif isinstance(item, Decimal):
            numbers.append(float(item))
        elif isinstance(item, str):
            numbers.extend(n.value for n in extract_numbers(item))
        elif isinstance(item, dict):
            for child in item.values():
                walk(child, depth + 1)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child, depth + 1)

    walk(value, 0)
    return numbers


def _matches(found: FoundNumber, source: float) -> bool:
    candidates = [source]
    if found.percent:
        candidates.append(source * 100)  # 0.123 written as 12.3%
    for candidate in candidates:
        for sign in (1, -1):  # "a drop of 5%" for -5
            target = candidate * sign
            # rounding to the precision shown (1.2M allows ±0.05M) or 0.5% of the value
            tolerance = max(0.5 * 10 ** (-found.decimals) * found.scale, abs(target) * 0.005)
            if abs(found.value - target) <= tolerance:
                return True
    return False


def untraced_numbers(answer: str, tool_results: list[Any], question: str = "") -> list[str]:
    """Return the numbers written in `answer` that no tool result of the turn supports."""
    body = _CAVEAT_TAIL.sub("", answer)
    sources = source_numbers(tool_results)
    asked = {round(n.value, 6) for n in extract_numbers(question)}
    missing: list[str] = []
    for found in extract_numbers(body):
        if found.decimals == 0 and not found.percent and 0 <= found.value <= EXEMPT_MAX:
            continue
        if round(found.value, 6) in asked:
            continue
        if not any(_matches(found, source) for source in sources):
            missing.append(found.text)
    return list(dict.fromkeys(missing))


def caveat_text(numbers: list[str]) -> str:
    """Return the caveat appended to an answer with untraced numbers."""
    shown = ", ".join(numbers[:10])
    return (
        f"Caveat: these numbers could not be traced to a tool result in this answer and may be "
        f"wrong: {shown}."
    )
