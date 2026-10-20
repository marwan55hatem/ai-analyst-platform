"""Time-series anomaly maths: robust z on STL residuals, Isolation Forest, events, level shifts.

Everything here works on small numpy arrays (one value per day, week or month) that the module
aggregated in DuckDB. Nothing reads data or formats text.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from statsmodels.tsa.seasonal import STL

Z_THRESHOLD = 3.5
# share of zero days above which the log scale would turn ordinary quiet days into "drops"
ZERO_SHARE_FOR_RAW = 0.05
STL_MIN_DAYS = 56
FOREST_MIN_POINTS = 90
COARSE_MIN_POINTS = 26
ROLLING_WINDOW = 5
STL_SEASONAL = 31
FOREST_TREES = 200
# alert budget: the forest marks about 1% of periods (at least 3) as unusual
FOREST_BASE_RATE = 0.01
FOREST_MIN_FLAGS = 3
FOREST_MAX_RATE = 0.05
LEVEL_SHIFT_MIN_WEEKS = 65
LEVEL_SHIFT_MIN_SEGMENT = 4
# a max-over-splits statistic: about 3.5 is a 1% false-alarm level for 50-100 weekly points
LEVEL_SHIFT_MIN_STAT = 3.5
LEVEL_SHIFT_MIN_CHANGE = 0.15
WEEKS_PER_YEAR = 52
MIRROR_TOLERANCE_WEEKS = 3


@dataclass(frozen=True)
class MethodPlan:
    """Which methods a series of this length and spacing supports."""

    unit: str  # "day", "week" or "month"
    baseline: str  # "stl" or "rolling_median"
    forest: bool
    period: int | None


@dataclass
class Scored:
    """A series with its expected values, robust z-scores and the forest's verdict per point."""

    actual: np.ndarray
    expected: np.ndarray
    z: np.ndarray
    forest: np.ndarray | None
    scale: str  # "log" or "raw"
    method: str

    @property
    def flagged(self) -> np.ndarray:
        """Points reported as anomalies: robust z beyond the threshold, confirmed by the forest."""
        strong = np.abs(self.z) >= Z_THRESHOLD
        flags = strong if self.forest is None else strong & self.forest
        return np.asarray(flags, dtype=bool)


@dataclass
class Event:
    """One anomaly: consecutive flagged points with the same direction."""

    start: int
    end: int  # inclusive
    kind: str  # "spike" or "drop"
    actual: float
    expected: float
    peak_z: float
    methods: list[str] = field(default_factory=list)

    @property
    def length(self) -> int:
        """How many consecutive periods the anomaly lasted."""
        return self.end - self.start + 1

    @property
    def change(self) -> float:
        """Actual minus expected over the whole event (money)."""
        return self.actual - self.expected

    @property
    def rank_score(self) -> float:
        """Magnitude times persistence: what the ranking sorts on."""
        return abs(self.change) * self.length


@dataclass(frozen=True)
class LevelShift:
    """A lasting change of level, measured on year-over-year weekly ratios."""

    week: int  # index in the weekly series where the new level starts
    change_pct: float
    statistic: float
    weeks_after: int

    @property
    def kind(self) -> str:
        """Direction of the shift."""
        return "level_shift_up" if self.change_pct > 0 else "level_shift_down"


def plan_for(unit: str, points: int) -> MethodPlan | None:
    """Choose the methods for a series; None means too short to say anything."""
    if unit == "day":
        if points < STL_MIN_DAYS:
            return None
        return MethodPlan("day", "stl", points >= FOREST_MIN_POINTS, 7)
    if points < COARSE_MIN_POINTS:
        return None
    return MethodPlan(unit, "rolling_median", points >= FOREST_MIN_POINTS, None)


def robust_z(values: np.ndarray) -> np.ndarray:
    """Return 0.6745 * (x - median) / MAD; all zeros when MAD is zero (no spread to judge by)."""
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad == 0.0:
        return np.zeros_like(values, dtype=float)
    return 0.6745 * (values - median) / mad


def _transform(values: np.ndarray) -> tuple[np.ndarray, str]:
    clipped = np.clip(values, 0.0, None)
    if np.mean(clipped == 0.0) > ZERO_SHARE_FOR_RAW:
        return values.astype(float), "raw"
    return np.log1p(clipped), "log"


def _untransform(values: np.ndarray, scale: str) -> np.ndarray:
    return np.expm1(values) if scale == "log" else values


def baseline(values: np.ndarray, plan: MethodPlan) -> np.ndarray:
    """Return the expected value per point (on the transformed scale)."""
    if plan.baseline == "stl" and plan.period is not None:
        fit = STL(values, period=plan.period, seasonal=STL_SEASONAL, robust=True).fit()
        return np.asarray(fit.trend + fit.seasonal, dtype=float)
    series = pd.Series(values)
    rolled = series.rolling(ROLLING_WINDOW, center=True, min_periods=ROLLING_WINDOW // 2 + 1)
    return np.asarray(rolled.median().bfill().ffill(), dtype=float)


def forest_flags(features: np.ndarray, seed: int) -> np.ndarray:
    """Isolation Forest on standardized features; True marks the most isolated points."""
    n = features.shape[0]
    spread = features.std(axis=0)
    spread[spread == 0] = 1.0
    scaled = (features - np.median(features, axis=0)) / spread
    rate = min(FOREST_MAX_RATE, max(FOREST_BASE_RATE, FOREST_MIN_FLAGS / n))
    forest = IsolationForest(n_estimators=FOREST_TREES, contamination=rate, random_state=seed)
    return np.asarray(forest.fit_predict(scaled) == -1)


def score(revenue: np.ndarray, plan: MethodPlan, extra: dict[str, np.ndarray], seed: int) -> Scored:
    """Score every point of a revenue series; `extra` holds other daily features for the forest.

    Each extra feature is turned into a residual with the same baseline as revenue, so the forest
    sees "unusual for this time of year and weekday", not "a busy season".
    """
    transformed, scale = _transform(revenue)
    expected_t = baseline(transformed, plan)
    resid = transformed - expected_t
    z = robust_z(resid)
    forest = None
    method = (
        f"STL (period {plan.period}, robust) residuals"
        if plan.baseline == "stl"
        else f"residuals from a centred {ROLLING_WINDOW}-{plan.unit} rolling median"
    )
    method += f" of {scale} revenue scored with a robust z (median/MAD, |z| >= {Z_THRESHOLD})"
    if plan.forest:
        columns = [resid]
        for values in extra.values():
            t, _ = _transform(values) if np.all(values >= 0) else (values, "raw")
            columns.append(t - baseline(t, plan))
        forest = forest_flags(np.column_stack(columns), seed)
        names = ", ".join(["revenue", *extra])
        method += (
            f", confirmed by an Isolation Forest on {names} residuals "
            f"({FOREST_TREES} trees, fixed seed)"
        )
    return Scored(
        actual=revenue.astype(float),
        expected=_untransform(expected_t, scale),
        z=z,
        forest=forest,
        scale=scale,
        method=method,
    )


def events(scored: Scored) -> list[Event]:
    """Merge consecutive flagged points of the same direction into events, largest first."""
    flags = scored.flagged
    found: list[Event] = []
    i, n = 0, len(flags)
    while i < n:
        if not flags[i]:
            i += 1
            continue
        sign = np.sign(scored.z[i])
        j = i
        while j + 1 < n and flags[j + 1] and np.sign(scored.z[j + 1]) == sign:
            j += 1
        span = slice(i, j + 1)
        methods = ["robust_z"] + (["isolation_forest"] if scored.forest is not None else [])
        found.append(
            Event(
                start=i,
                end=j,
                kind="spike" if sign > 0 else "drop",
                actual=float(scored.actual[span].sum()),
                expected=float(scored.expected[span].sum()),
                peak_z=float(scored.z[span][np.argmax(np.abs(scored.z[span]))]),
                methods=methods,
            )
        )
        i = j + 1
    return sorted(found, key=lambda e: (-e.rank_score, e.start))


def single_method_points(scored: Scored) -> dict[str, list[int]]:
    """Points only one method found: reported as a watch list, never as anomalies."""
    strong = np.abs(scored.z) >= Z_THRESHOLD
    if scored.forest is None:
        return {"z_only": [], "forest_only": []}
    return {
        "z_only": [int(i) for i in np.where(strong & ~scored.forest)[0]],
        "forest_only": [int(i) for i in np.where(scored.forest & ~strong)[0]],
    }


def winsorize_daily(values: np.ndarray, window: int = 29, limit: float = 3.0) -> np.ndarray:
    """Cap each positive day at `limit` robust deviations from its rolling median (log scale).

    Weekly sums of a business with few, large orders are dominated by single big orders; capping
    them first lets a lasting change of level show through.
    """
    logs = np.log1p(np.clip(values, 0.0, None))
    centre = pd.Series(logs).rolling(window, center=True, min_periods=window // 4).median()
    centre_values = np.asarray(centre.bfill().ffill(), dtype=float)
    resid = logs - centre_values
    mad = float(np.median(np.abs(resid - np.median(resid)))) / 0.6745
    if mad == 0.0:
        return values.astype(float)
    capped = np.expm1(centre_values + np.clip(resid, -limit * mad, limit * mad))
    return np.where(values > 0, capped, values).astype(float)


def _winsorize(values: np.ndarray, scale: float, limit: float = 4.0) -> np.ndarray:
    center = float(np.median(values))
    return np.asarray(np.clip(values, center - limit * scale, center + limit * scale), dtype=float)


def _noise(values: np.ndarray) -> float:
    """Robust noise level of a series, from its first differences (steps barely move it)."""
    diffs = np.diff(values)
    if len(diffs) == 0:
        return 0.0
    mad = float(np.median(np.abs(diffs - np.median(diffs))))
    return float(1.4826 * mad / np.sqrt(2.0))


def _best_split(values: np.ndarray, noise: float) -> tuple[int, float] | None:
    """CUSUM: the split that maximises the cumulative deviation, with its t-like statistic."""
    n = len(values)
    if n < 2 * LEVEL_SHIFT_MIN_SEGMENT or noise == 0.0:
        return None
    cusum = np.cumsum(values - values.mean())
    candidates = range(LEVEL_SHIFT_MIN_SEGMENT, n - LEVEL_SHIFT_MIN_SEGMENT + 1)
    k = max(candidates, key=lambda c: (abs(cusum[c - 1]), -c))
    left, right = values[:k], values[k:]
    stat = abs(right.mean() - left.mean()) / (noise * np.sqrt(1 / len(left) + 1 / len(right)))
    return k, float(stat)


def level_shifts(weekly: np.ndarray) -> list[LevelShift]:
    """Find lasting level changes in a weekly revenue series (needs 65+ weeks).

    Year-over-year log ratios remove the yearly season; a CUSUM split (binary segmentation, two
    levels deep) finds where the ratio moves to a new level and stays there for 4+ weeks. A real
    shift reappears, mirrored, 52 weeks later (the new level becomes the comparison base), so a
    change of opposite sign about a year after an accepted one is ignored.
    """
    if len(weekly) < LEVEL_SHIFT_MIN_WEEKS:
        return []
    logs = np.log1p(np.clip(weekly, 0.0, None))
    ratios = logs[WEEKS_PER_YEAR:] - logs[:-WEEKS_PER_YEAR]
    noise = _noise(ratios)
    if noise == 0.0:
        return []
    clean = _winsorize(ratios, noise)
    found: list[tuple[int, float]] = []

    def split(lo: int, hi: int, depth: int) -> None:
        best = _best_split(clean[lo:hi], noise)
        if best is None:
            return
        k, stat = best
        before, after = clean[lo : lo + k], clean[lo + k : hi]
        change = float(np.expm1(np.median(after) - np.median(before)))
        if stat < LEVEL_SHIFT_MIN_STAT or abs(change) < LEVEL_SHIFT_MIN_CHANGE:
            return
        found.append((lo + k, stat))
        if depth < 2:
            split(lo, lo + k, depth + 1)
            split(lo + k, hi, depth + 1)

    split(0, len(clean), 1)
    shifts: list[LevelShift] = []
    bounds = sorted(k for k, _ in found)
    for k, stat in sorted(found):
        nxt = next((b for b in bounds if b > k), len(clean))
        prev = max((b for b in bounds if b < k), default=0)
        change = float(np.expm1(np.median(clean[k:nxt]) - np.median(clean[prev:k])))
        mirrored = any(
            # s.week is an index in the weekly series; k + 52 is this change's index there
            abs(k - s.week) <= MIRROR_TOLERANCE_WEEKS and np.sign(s.change_pct) != np.sign(change)
            for s in shifts
        )
        if not mirrored:
            shifts.append(LevelShift(k + WEEKS_PER_YEAR, 100.0 * change, stat, nxt - k))
    return shifts
