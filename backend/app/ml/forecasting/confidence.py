"""How much to trust a forecast: derived from backtest MASE, history length and interval coverage.

The rules are written in docs/ml-methods.md and tested one by one. A result can only be
"high" when it clearly beats the naive baseline AND there are three full seasonal cycles.
"""

from typing import Literal

Confidence = Literal["high", "medium", "low"]
ORDER: tuple[Confidence, ...] = ("low", "medium", "high")
HIGH_MASE = 0.7
MEDIUM_MASE = 1.0
# An 80% interval that really covers far less or far more than 80% of backtest points is
# miscalibrated, so confidence drops one level.
COVERAGE_OK = (0.6, 0.95)


def lower(level: Confidence) -> Confidence:
    """Return the next lower confidence level (low stays low)."""
    return ORDER[max(0, ORDER.index(level) - 1)]


def derive_confidence(
    *,
    mase: float | None,
    beats_baseline: bool,
    cycles: float,
    coverage80: float | None,
) -> tuple[Confidence, list[str]]:
    """Return (confidence, reasons it is not higher)."""
    reasons: list[str] = []
    if mase is None:
        return "low", ["there was too little history to test the forecast on past data"]
    if not beats_baseline:
        return "low", ["it was no better than simply repeating last year's pattern"]
    if cycles < 2:
        level: Confidence = "low"
        reasons.append("fewer than two full seasonal cycles of history")
    elif mase <= HIGH_MASE and cycles >= 3:
        level = "high"
    elif mase <= MEDIUM_MASE:
        level = "medium"
        if mase > HIGH_MASE:
            reasons.append("its backtest error was only moderately below the baseline")
        if cycles < 3:
            reasons.append("less than three full seasonal cycles of history")
    else:
        level = "low"
        reasons.append("its backtest error was high")
    if coverage80 is not None and not COVERAGE_OK[0] <= coverage80 <= COVERAGE_OK[1]:
        level = lower(level)
        reasons.append("its 80% range did not cover about 80% of past outcomes")
    return level, list(dict.fromkeys(reasons))
