"""Series preparation: frequency choice, complete periods, gap filling and outlier capping.

Aggregation happens in DuckDB (the SQL is built here and returned as evidence); only the small
per-period result reaches pandas. Every adjustment made for training is recorded as a warning.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, cast

import numpy as np
import pandas as pd

# 1.4826 turns a median absolute deviation into a standard deviation for normal data
MAD_TO_SIGMA = 1.4826
OUTLIER_SIGMAS = 5.0
MIN_PERIODS = 3


@dataclass(frozen=True)
class Freq:
    """One aggregation level: how DuckDB truncates, how pandas indexes, the seasonal cycle."""

    key: str
    pandas: str
    season: int
    noun: str
    plural: str
    trunc: str


DAY = Freq("day", "D", 7, "day", "days", "day")
WEEK = Freq("week", "W-MON", 52, "week", "weeks", "week")
MONTH = Freq("month", "MS", 12, "month", "months", "month")
QUARTER = Freq("quarter", "QS", 4, "quarter", "quarters", "quarter")
FREQS = {f.key: f for f in (DAY, WEEK, MONTH, QUARTER)}


def choose_frequency(granularity: str | None, first: date, last: date) -> Freq | None:
    """Pick the aggregation level: the coarsest one that still leaves a usable history.

    Daily or weekly rows are rolled up to months once there is a year of data (to weeks after
    half a year), because a monthly figure is what a business plans with. Yearly data cannot be
    forecast with so few periods, so None is returned.
    """
    span = (last - first).days + 1
    if granularity == "yearly":
        return None
    if granularity == "quarterly":
        return QUARTER
    if granularity == "monthly":
        return MONTH
    if span >= 365:
        return MONTH
    if granularity == "weekly" or span >= 182:
        return WEEK
    return DAY


def period_start(day: date, freq: Freq) -> date:
    """Return the first day of the period containing `day`."""
    if freq.key == "day":
        return day
    if freq.key == "week":
        return day - timedelta(days=day.weekday())
    if freq.key == "month":
        return day.replace(day=1)
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def next_period(start: date, freq: Freq) -> date:
    """Return the first day of the period after the one starting at `start`."""
    if freq.key == "day":
        return start + timedelta(days=1)
    if freq.key == "week":
        return start + timedelta(days=7)
    months = 1 if freq.key == "month" else 3
    total = start.year * 12 + (start.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def is_complete(last_day: date, freq: Freq) -> bool:
    """Return whether data ending on `last_day` covers the whole of its period."""
    return next_period(period_start(last_day, freq), freq) == last_day + timedelta(days=1)


def period_label(start: date, freq: Freq) -> str:
    """Return a short human label such as 'Mar 2025', 'week of 3 Mar 2025' or '2025 Q1'."""
    if freq.key == "month":
        return start.strftime("%b %Y")
    if freq.key == "quarter":
        return f"{start.year} Q{(start.month - 1) // 3 + 1}"
    if freq.key == "week":
        return f"week of {start.day} {start.strftime('%b %Y')}"
    return start.isoformat()


def history_sql(
    value_expr: str,
    date_col: str,
    freq: Freq,
    group_col: str | None = None,
    groups: list[str] | None = None,
) -> str:
    """Return the DuckDB query that aggregates net values per period (and per group)."""
    where = f"{value_expr} IS NOT NULL AND {date_col} IS NOT NULL"
    period = f"CAST(date_trunc('{freq.trunc}', {date_col}) AS DATE) AS period"
    if group_col is None:
        return (
            f"SELECT {period}, sum({value_expr}) AS value, count(*) AS rows FROM data "
            f"WHERE {where} GROUP BY 1 ORDER BY 1"
        )
    where += f" AND {group_col} IS NOT NULL"
    if groups is not None:
        listed = ", ".join("'" + g.replace("'", "''") + "'" for g in groups)
        where += f" AND {group_col} IN ({listed})"
    return (
        f"SELECT {period}, {group_col} AS grp, sum({value_expr}) AS value, count(*) AS rows "
        f"FROM data WHERE {where} GROUP BY 1, 2 ORDER BY 2, 1"
    )


@dataclass
class PreparedSeries:
    """One series ready for modelling, with the notes about how it was prepared."""

    name: str
    freq: Freq
    index: pd.DatetimeIndex
    raw: np.ndarray
    train: np.ndarray
    rows: int
    warnings: list[str] = field(default_factory=list)
    non_negative: bool = True

    @property
    def n(self) -> int:
        """Number of periods in the series."""
        return len(self.raw)

    @property
    def season(self) -> int:
        """Seasonal cycle length in periods."""
        return self.freq.season

    @property
    def cycles(self) -> float:
        """Number of full seasonal cycles of history."""
        return self.n / self.season if self.season > 1 else float(self.n)

    @property
    def is_constant(self) -> bool:
        """Whether every training value is the same."""
        return bool(np.ptp(self.train) == 0)

    @property
    def last_period(self) -> date:
        """Start date of the last observed period."""
        return date(*self.index[-1].timetuple()[:3])


def prepare_series(
    name: str,
    rows: list[dict[str, Any]],
    freq: Freq,
    last_data_day: date,
    *,
    fill_zero: bool = True,
) -> PreparedSeries | None:
    """Turn aggregated rows into a modelling series, or None when there is too little data.

    Steps, each recorded as a warning when it changes anything: drop an incomplete trailing
    period (it would bias every forecast low), fill missing periods with zero (no sales that
    period), and cap extreme outliers for training only (the raw series is kept for display).
    """
    if not rows:
        return None
    warnings: list[str] = []
    by_period: dict[date, float] = {}
    row_count = 0
    for r in rows:
        by_period[_as_date(r["period"])] = float(r["value"] or 0.0)
        row_count += int(r.get("rows", 0))
    last_start = max(by_period)
    if not is_complete(last_data_day, freq) and period_start(last_data_day, freq) == last_start:
        by_period.pop(last_start)
        warnings.append(
            f"The data ends part-way through {period_label(last_start, freq)}, so that "
            f"incomplete {freq.noun} was left out of training; including it would pull the "
            "forecast down."
        )
    if len(by_period) < MIN_PERIODS:
        return None
    index = pd.date_range(min(by_period), max(by_period), freq=freq.pandas)
    series = pd.Series({pd.Timestamp(k): v for k, v in by_period.items()}).reindex(index)
    gaps = int(series.isna().sum())
    if gaps:
        series = series.fillna(0.0) if fill_zero else series.interpolate(limit_direction="both")
        how = "zero (no sales that period)" if fill_zero else "interpolation"
        warnings.append(f"{gaps} {freq.noun}(s) had no records and were filled with {how}.")
    raw = series.to_numpy(dtype=float)
    train, capped = cap_outliers(raw, freq.season)
    if capped:
        warnings.append(
            f"{capped} extreme value(s) were capped for training only (more than "
            f"{OUTLIER_SIGMAS:.0f} robust deviations from the local level); the chart shows "
            "the real values."
        )
    return PreparedSeries(
        name=name,
        freq=freq,
        index=index,
        raw=raw,
        train=train,
        rows=row_count,
        warnings=warnings,
        non_negative=bool(raw.min() >= 0),
    )


def cap_outliers(values: np.ndarray, season: int) -> tuple[np.ndarray, int]:
    """Cap values far from their expected level; returns (capped copy, number capped).

    With at least two seasonal cycles the expected level is a linear trend plus the median of each
    seasonal position, fitted on log values when the series is non-negative (so a normal December
    spike in a growing business is never mistaken for an outlier). With less history a centred
    rolling median is used. Only the remainder is capped, at OUTLIER_SIGMAS robust deviations.
    """
    n = len(values)
    if n < 8:
        return values.copy(), 0
    log = bool(values.min() >= 0)
    work = np.log1p(values) if log else values.astype(float)
    if season > 1 and n >= 2 * season:
        t = np.arange(n, dtype=float)
        slope, intercept = np.polyfit(t, work, 1)
        detrended = work - (intercept + slope * t)
        phase = np.arange(n) % season
        seasonal = np.array([np.median(detrended[phase == p]) for p in range(season)])
        level = intercept + slope * t + seasonal[phase]
    else:
        level = pd.Series(work).rolling(5, center=True, min_periods=1).median().to_numpy()
    resid = work - level
    mad = float(np.median(np.abs(resid - np.median(resid))))
    if mad == 0:
        return values.copy(), 0
    limit = OUTLIER_SIGMAS * MAD_TO_SIGMA * mad
    changed = int(np.sum(np.abs(resid) > limit))
    if changed == 0:
        return values.copy(), 0
    capped = level + np.clip(resid, -limit, limit)
    return (np.expm1(capped) if log else capped), changed


def _as_date(value: Any) -> date:
    return value.date() if isinstance(value, datetime) else cast(date, value)
