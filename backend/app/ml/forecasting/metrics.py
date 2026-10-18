"""Forecast accuracy metrics: sMAPE, MASE and MAE, plus interval coverage."""

import numpy as np


def mae(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Mean absolute error."""
    return float(np.mean(np.abs(actual - predicted)))


def smape(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Symmetric MAPE in percent (0 to 200); a period where both values are 0 counts as 0."""
    denom = np.abs(actual) + np.abs(predicted)
    ratio = np.divide(
        2.0 * np.abs(actual - predicted), denom, out=np.zeros_like(denom), where=denom > 0
    )
    return float(100.0 * np.mean(ratio))


def naive_scale(history: np.ndarray, season: int) -> float:
    """MAE of the seasonal naive forecast inside the history (the MASE denominator).

    Uses lag `season` when there is more than one cycle, else lag 1. Returns 0.0 when the
    history never changes, in which case MASE is undefined.
    """
    lag = season if 1 < season < len(history) else 1
    if len(history) <= lag:
        return 0.0
    return float(np.mean(np.abs(history[lag:] - history[:-lag])))


def mase(actual: np.ndarray, predicted: np.ndarray, scale: float) -> float:
    """MAE divided by the in-sample seasonal-naive MAE; below 1 beats the naive baseline."""
    if scale <= 0:
        return float("inf") if mae(actual, predicted) > 0 else 0.0
    return mae(actual, predicted) / scale


def coverage(actual: np.ndarray, low: np.ndarray, high: np.ndarray) -> float:
    """Share of actual values that fall inside [low, high]."""
    return float(np.mean((actual >= low) & (actual <= high)))
