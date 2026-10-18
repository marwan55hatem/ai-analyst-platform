"""Candidate models: seasonal naive baseline, ETS, AutoARIMA and Theta (statsforecast), LightGBM.

Every candidate returns point forecasts with 80% and 95% intervals. The statistical models use
their own model-based intervals; LightGBM uses the spread of its own earlier backtest errors.
"""

import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.ml.forecasting.series import Freq

Z80 = 1.2816
Z95 = 1.9600
BASELINE = "SeasonalNaive"
# Seasonal ARIMA/ETS get very slow and memory hungry with long cycles (weekly = 52)
MAX_SEASON_FOR_ARIMA = 24
LGBM_MIN_ROWS = 8


@dataclass
class Forecast:
    """Point forecast and interval bounds for `h` future periods."""

    point: np.ndarray
    lo80: np.ndarray
    hi80: np.ndarray
    lo95: np.ndarray
    hi95: np.ndarray

    def finite(self) -> bool:
        """Whether every value is a real number."""
        return all(
            np.isfinite(a).all() for a in (self.point, self.lo80, self.hi80, self.lo95, self.hi95)
        )

    def clipped(self, floor: float | None) -> "Forecast":
        """Return a copy with all values at or above `floor` (None keeps them as they are)."""
        if floor is None:
            return self
        return Forecast(*(np.maximum(a, floor) for a in self.arrays()))

    def arrays(self) -> tuple[np.ndarray, ...]:
        """Return the five arrays in a fixed order."""
        return (self.point, self.lo80, self.hi80, self.lo95, self.hi95)


@dataclass(frozen=True)
class Candidate:
    """A model that may enter the backtest. `heavy` models are skipped first under pressure."""

    name: str
    kind: str  # "stat" or "lgbm"
    heavy: bool = False
    factory: Callable[[], Any] | None = None


def _stat(name: str, season: int) -> Candidate:
    from statsforecast.models import AutoARIMA, AutoETS, AutoTheta, Naive, SeasonalNaive

    makers: dict[str, Callable[[], Any]] = {
        BASELINE: lambda: SeasonalNaive(season_length=season, alias=BASELINE),
        "Naive": lambda: Naive(alias="Naive"),
        "AutoETS": lambda: AutoETS(season_length=season, alias="AutoETS"),
        "AutoTheta": lambda: AutoTheta(season_length=season, alias="AutoTheta"),
        "AutoARIMA": lambda: AutoARIMA(season_length=season, alias="AutoARIMA"),
    }
    return Candidate(name, "stat", False, makers[name])


def candidate_list(n: int, season: int) -> list[Candidate]:
    """Return the candidates for a series of `n` periods, cheapest first, baseline first.

    With fewer than two full seasonal cycles only simple models are used (the seasonal pattern
    cannot be learned reliably), and the baseline falls back to plain naive below one cycle.
    """
    if season <= 1 or n < 2 * season:
        base = BASELINE if season > 1 and n >= season else "Naive"
        out = [_stat(base, season if base == BASELINE else 1)]
        if base != "Naive":
            out.append(_stat("Naive", 1))
        out.append(_stat("AutoETS", 1))
        out.append(_stat("AutoTheta", 1))
        return out
    out = [_stat(BASELINE, season), _stat("AutoTheta", season)]
    out.append(_stat("AutoETS", season if season <= MAX_SEASON_FOR_ARIMA else 1))
    if season <= MAX_SEASON_FOR_ARIMA:
        out.append(_stat("AutoARIMA", season))
    out.append(Candidate("LightGBM", "lgbm", True, None))
    return out


def baseline_name(candidates: list[Candidate]) -> str:
    """Name of the baseline candidate (always first)."""
    return candidates[0].name


def _frame(y: np.ndarray, index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({"unique_id": "s", "ds": index, "y": y})


def _parse(frame: pd.DataFrame, name: str) -> Forecast:
    def col(suffix: str) -> np.ndarray:
        return np.asarray(frame[f"{name}{suffix}"].to_numpy(), dtype=float)

    return Forecast(col(""), col("-lo-80"), col("-hi-80"), col("-lo-95"), col("-hi-95"))


def stat_forecast(
    cand: Candidate, y: np.ndarray, index: pd.DatetimeIndex, freq: Freq, h: int
) -> Forecast:
    """Fit a statsforecast model on the whole series and forecast `h` periods."""
    from statsforecast import StatsForecast

    assert cand.factory is not None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sf = StatsForecast(models=[cand.factory()], freq=freq.pandas, n_jobs=1)
        out = sf.forecast(df=_frame(y, index), h=h, level=[80, 95])
    return _parse(out, cand.name)


def stat_backtest(
    cand: Candidate,
    y: np.ndarray,
    index: pd.DatetimeIndex,
    freq: Freq,
    h: int,
    step: int,
    windows: int,
) -> list[Forecast]:
    """Rolling-origin (expanding window) backtest; returns one Forecast per fold, oldest first."""
    from statsforecast import StatsForecast

    assert cand.factory is not None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sf = StatsForecast(models=[cand.factory()], freq=freq.pandas, n_jobs=1)
        cv = sf.cross_validation(
            df=_frame(y, index), h=h, step_size=step, n_windows=windows, level=[80, 95]
        )
    return [
        _parse(part.sort_values("ds"), cand.name) for _, part in cv.groupby("cutoff", sort=True)
    ]


# ---------------------------------------------------------------- LightGBM


def _lags(season: int, n: int) -> list[int]:
    lags = {1, 2, 3}
    if 1 < season <= n // 3:
        lags.add(season)
    return sorted(lags)


def _calendar(ts: pd.Timestamp, freq: Freq) -> list[float]:
    if freq.key == "day":
        return [float(ts.dayofweek), float(ts.day), float(ts.month)]
    if freq.key == "week":
        return [float(ts.isocalendar().week), float(ts.month)]
    if freq.key == "month":
        return [float(ts.month)]
    return [float(ts.quarter)]


def _row(resid: list[float], lags: list[int], ts: pd.Timestamp, freq: Freq) -> list[float]:
    window = resid[-3:]
    return [resid[-lag] for lag in lags] + [float(np.mean(window))] + _calendar(ts, freq)


def lgbm_applicable(n: int, season: int) -> bool:
    """Whether there is enough history to train LightGBM with its lags."""
    return n - max(_lags(season, n)) >= LGBM_MIN_ROWS


def lgbm_forecast(
    y: np.ndarray,
    index: pd.DatetimeIndex,
    freq: Freq,
    h: int,
    seed: int,
    error_pool: np.ndarray | None,
) -> Forecast:
    """Linear trend plus LightGBM on lagged, rolling and calendar features, forecast recursively.

    `error_pool` holds the earlier backtest errors of this model; their root-mean-square sets the
    interval width. With no earlier errors the training residuals are used, inflated by 1.5
    because in-sample errors are optimistic.
    """
    import lightgbm as lgb

    n, season = len(y), freq.season
    lags = _lags(season, n)
    t = np.arange(n, dtype=float)
    slope, intercept = np.polyfit(t, y, 1)
    resid = (y - (intercept + slope * t)).tolist()
    start = max(lags)
    rows = [_row(resid[:i], lags, index[i], freq) for i in range(start, n)]
    target = np.asarray(resid[start:])
    params = {
        "objective": "regression",
        "learning_rate": 0.05,
        "num_leaves": 8,
        "min_data_in_leaf": 3,
        "min_data_in_bin": 1,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "feature_fraction": 0.9,
        "seed": seed,
        "num_threads": 1,
        "deterministic": True,
        "force_row_wise": True,
        "verbosity": -1,
    }
    matrix = np.asarray(rows)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = lgb.train(params, lgb.Dataset(matrix, label=target), num_boost_round=150)
        fitted = model.predict(matrix)
        future = pd.date_range(index[-1], periods=h + 1, freq=freq.pandas)[1:]
        preds: list[float] = []
        history = list(resid)
        for ts in future:
            nxt = float(model.predict(np.asarray([_row(history, lags, ts, freq)]))[0])
            preds.append(nxt)
            history.append(nxt)
    steps = np.arange(n, n + h, dtype=float)
    point = intercept + slope * steps + np.asarray(preds)
    if error_pool is not None and len(error_pool) >= 3:
        sigma = float(np.sqrt(np.mean(np.square(error_pool))))
    else:
        sigma = 1.5 * float(np.sqrt(np.mean(np.square(target - fitted))))
    return Forecast(
        point, point - Z80 * sigma, point + Z80 * sigma, point - Z95 * sigma, point + Z95 * sigma
    )


def lgbm_backtest(
    y: np.ndarray,
    index: pd.DatetimeIndex,
    freq: Freq,
    h: int,
    cutoffs: list[int],
    seed: int,
) -> list[Forecast]:
    """Backtest LightGBM at each cutoff; each fold's interval uses only earlier folds' errors."""
    out: list[Forecast] = []
    pool: list[float] = []
    for cut in cutoffs:
        fc = lgbm_forecast(y[:cut], index[:cut], freq, h, seed, np.asarray(pool) if pool else None)
        out.append(fc)
        pool.extend((y[cut : cut + h] - fc.point).tolist())
    return out


def lgbm_error_pool(folds: list[Forecast], y: np.ndarray, cutoffs: list[int], h: int) -> np.ndarray:
    """All backtest errors of the LightGBM folds, for the final forecast's interval."""
    errors: list[float] = []
    for fc, cut in zip(folds, cutoffs, strict=True):
        errors.extend((y[cut : cut + h] - fc.point).tolist())
    return np.asarray(errors)
