"""Synthetic series and prepared-series builders for the forecasting tests."""

from datetime import date, timedelta

import numpy as np

from app.ml.forecasting.series import MONTH, Freq, PreparedSeries, prepare_series


def month_start(index: int, first: date = date(2020, 1, 1)) -> date:
    """Return the first day of the month `index` months after `first`."""
    total = first.year * 12 + (first.month - 1) + index
    return date(total // 12, total % 12 + 1, 1)


def monthly_values(
    n: int, seed: int = 1, noise: float = 0.04, trend: float = 8.0, amplitude: float = 0.25
) -> np.ndarray:
    """Trend + yearly seasonality + multiplicative noise, always positive."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    base = (1000 + trend * t) * (1 + amplitude * np.sin(2 * np.pi * t / 12))
    return np.asarray(base * (1 + rng.normal(0, noise, n)))


def rows_for(values: np.ndarray, freq: Freq = MONTH, first: date = date(2020, 1, 1)) -> list[dict]:
    """Aggregated rows as DuckDB would return them (period, value, rows)."""
    out = []
    current = first
    for v in values:
        out.append({"period": current, "value": float(v), "rows": 10})
        if freq.key == "day":
            current += timedelta(days=1)
        elif freq.key == "week":
            current += timedelta(days=7)
        else:
            current = month_start(1, current)
    return out


def last_day_of(rows: list[dict], freq: Freq = MONTH) -> date:
    """The last calendar day covered by the final period (a complete period)."""
    last = rows[-1]["period"]
    if freq.key == "day":
        return last
    if freq.key == "week":
        return last + timedelta(days=6)
    return month_start(1, last) - timedelta(days=1)


def prepared(values: np.ndarray, freq: Freq = MONTH, name: str = "Total") -> PreparedSeries:
    """Build a PreparedSeries from plain values (complete periods, no adjustments needed)."""
    rows = rows_for(values, freq)
    result = prepare_series(name, rows, freq, last_day_of(rows, freq))
    assert result is not None
    return result
