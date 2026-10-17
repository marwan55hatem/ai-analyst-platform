"""Impact score: estimated money effect x confidence weight, normalized to 0-100."""

CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.6, "low": 0.3}
# An effect worth this share of the dataset's revenue already scores the maximum.
SATURATION_SHARE = 0.25


def impact_score(money_effect: float, base_revenue: float, confidence: str) -> float:
    """Return 0-100: |effect| / base revenue (saturating at 25%) x confidence weight."""
    if base_revenue <= 0:
        return 0.0
    share = min(1.0, abs(money_effect) / base_revenue / SATURATION_SHARE)
    return round(100.0 * share * CONFIDENCE_WEIGHT.get(confidence, 0.3), 1)
