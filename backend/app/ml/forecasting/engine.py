"""Forecast one series: backtest every candidate, pick the winner by MASE, report honestly."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from app.ml.budget import Budget, reason_text
from app.ml.forecasting.confidence import Confidence, derive_confidence
from app.ml.forecasting.metrics import coverage, mae, mase, naive_scale, smape
from app.ml.forecasting.models import (
    Candidate,
    Forecast,
    candidate_list,
    lgbm_applicable,
    lgbm_backtest,
    lgbm_error_pool,
    lgbm_forecast,
    stat_backtest,
    stat_forecast,
)
from app.ml.forecasting.series import PreparedSeries, next_period

MAX_WINDOWS = 6
# A model must beat the baseline's MASE by at least this share to count as "better".
MIN_IMPROVEMENT = 0.02
Z80 = 1.2816
Z95 = 1.96


@dataclass
class ModelScore:
    """Backtest accuracy of one model, pooled over all folds."""

    name: str
    smape: float
    mase: float
    mae: float
    coverage80: float | None
    coverage95: float | None
    folds: int

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe view."""
        return {
            "model": self.name,
            "smape": _r(self.smape),
            "mase": _r(self.mase),
            "mae": _r(self.mae),
            "coverage80": _r(self.coverage80),
            "coverage95": _r(self.coverage95),
            "folds": self.folds,
        }


@dataclass
class ForecastResult:
    """Typed result of forecasting one series: value, confidence, sample_size, method, warnings."""

    series: str
    value: float
    confidence: Confidence
    sample_size: int
    method: str
    warnings: list[str]
    model: str
    periods: list[date]
    point: list[float]
    lo80: list[float]
    hi80: list[float]
    lo95: list[float]
    hi95: list[float]
    history_periods: list[date]
    history: list[float]
    scores: list[ModelScore] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    baseline_score: ModelScore | None = None
    winner_score: ModelScore | None = None
    no_better_than_baseline: bool = False
    confidence_reasons: list[str] = field(default_factory=list)
    cycles: float = 0.0
    backtest_horizon: int = 0
    backtest_windows: int = 0
    seed: int = 0
    freq_key: str = "month"
    constant: bool = False


def _r(value: float | None) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), 4)


def plan_folds(n: int, season: int, horizon: int) -> tuple[int, int, int] | None:
    """Return (backtest horizon, step, windows) for a series of n periods, or None if too short.

    The backtest horizon is the requested one, shrunk so that at least two thirds of the
    history, and preferably two seasonal cycles, are always available for training. Windows
    overlap by half a horizon to give more test points; at most six are used.
    """
    floor = max(6, season + 1) if season > 1 else 6
    bh = min(horizon, max(1, n // 4))
    while bh > 1 and n - 2 * bh < floor:
        bh -= 1
    first_train = max(floor, min(2 * season if season > 1 else floor, n - 2 * bh))
    step = max(1, bh // 2)
    if n - bh < first_train:
        return None
    windows = min(MAX_WINDOWS, (n - bh - first_train) // step + 1)
    return (bh, step, windows) if windows >= 1 else None


def _score(
    name: str,
    folds: list[Forecast],
    cuts: list[int],
    bh: int,
    actual: np.ndarray,
    scale: float,
) -> ModelScore:
    truth = np.concatenate([actual[c : c + bh] for c in cuts])
    pred = np.concatenate([f.point for f in folds])
    lo80 = np.concatenate([f.lo80 for f in folds])
    hi80 = np.concatenate([f.hi80 for f in folds])
    lo95 = np.concatenate([f.lo95 for f in folds])
    hi95 = np.concatenate([f.hi95 for f in folds])
    return ModelScore(
        name=name,
        smape=smape(truth, pred),
        mase=mase(truth, pred, scale),
        mae=mae(truth, pred),
        coverage80=coverage(truth, lo80, hi80),
        coverage95=coverage(truth, lo95, hi95),
        folds=len(folds),
    )


def _run_backtest(
    cand: Candidate,
    prep: PreparedSeries,
    bh: int,
    step: int,
    windows: int,
    cuts: list[int],
    seed: int,
) -> list[Forecast]:
    if cand.kind == "lgbm":
        return lgbm_backtest(prep.train, prep.index, prep.freq, bh, cuts, seed)
    folds = stat_backtest(cand, prep.train, prep.index, prep.freq, bh, step, windows)
    if len(folds) != len(cuts) or not all(f.finite() for f in folds):
        raise ValueError("the model returned incomplete or non-finite forecasts")
    return folds


def forecast_series(
    prep: PreparedSeries,
    horizon: int,
    budget: Budget,
    seed: int,
    *,
    allow_lightgbm: bool = True,
) -> ForecastResult:
    """Forecast `horizon` periods ahead, choosing the model by rolling-origin backtesting."""
    warnings = list(prep.warnings)
    freq = prep.freq
    n = prep.n
    future = _future_periods(prep, horizon)
    base_kwargs: dict[str, Any] = {
        "series": prep.name,
        "sample_size": prep.rows,
        "periods": future,
        "history_periods": [ts.date() for ts in prep.index],
        "history": [float(v) for v in prep.raw],
        "seed": seed,
        "freq_key": freq.key,
        "cycles": prep.cycles,
    }
    if prep.is_constant:
        warnings.append(
            f"Every {freq.noun} has the same value, so there is nothing to learn a pattern "
            "from; the forecast simply repeats it."
        )
        constant_value = float(prep.train[-1])
        flat = [constant_value] * horizon
        return ForecastResult(
            **base_kwargs,
            value=constant_value,
            confidence="low",
            method="repeat the constant value (no model could be tested)",
            warnings=warnings,
            model="Constant",
            point=flat,
            lo80=flat,
            hi80=flat,
            lo95=flat,
            hi95=flat,
            confidence_reasons=["the series never changes, so accuracy cannot be tested"],
            constant=True,
        )

    cands = candidate_list(n, prep.season)
    if not allow_lightgbm or not lgbm_applicable(n, prep.season):
        cands = [c for c in cands if c.kind != "lgbm"]
    if n < 2 * prep.season and prep.season > 1:
        warnings.append(
            f"Only {n} {freq.plural} of history (less than two full seasonal cycles of "
            f"{prep.season}), so only simple models were tried and confidence is low."
        )
    plan = plan_folds(n, prep.season, horizon)
    scale = naive_scale(prep.train, prep.season)
    scores: list[ModelScore] = []
    skipped: list[str] = []
    fold_cache: dict[str, list[Forecast]] = {}
    bh = step = windows = 0
    cuts: list[int] = []

    if plan is None:
        warnings.append(
            f"Only {n} {freq.plural} of history: too short to test any model on past data, "
            "so the baseline is used and confidence is low."
        )
        return _fallback(prep, horizon, cands[0], base_kwargs, warnings, skipped)

    bh, step, windows = plan
    cuts = [n - bh - k * step for k in range(windows - 1, -1, -1)]
    stop_reason: str | None = None
    for i, cand in enumerate(cands):
        if i > 0:
            stop_reason = budget.exhausted(0.4 if cand.heavy else 0.0)
            if stop_reason:
                skipped = [c.name for c in cands[i:]]
                warnings.append(
                    f"Because {reason_text(stop_reason, budget)}, these models were not tried: "
                    f"{', '.join(skipped)}."
                )
                break
        try:
            folds = _run_backtest(cand, prep, bh, step, windows, cuts, seed)
            fold_cache[cand.name] = folds
            scores.append(_score(cand.name, folds, cuts, bh, prep.raw, scale))
        except Exception as exc:  # a failing model must never fail the forecast
            if i == 0:
                raise
            warnings.append(
                f"{cand.name} could not be fitted ({type(exc).__name__}) and was skipped."
            )
            skipped.append(cand.name)

    baseline = scores[0]
    usable = [s for s in scores if np.isfinite(s.mase)]
    best = min(usable, key=lambda s: (s.mase, s.name)) if usable else baseline
    better = best.name != baseline.name and best.mase < baseline.mase * (1 - MIN_IMPROVEMENT)
    limited = len(scores) < 2 and stop_reason is not None  # the budget allowed no comparison
    no_better = not better and not limited
    chosen = best if better else baseline
    chosen_cand = next(c for c in cands if c.name == chosen.name)
    if no_better:
        warnings.append(
            "No model was clearly better than the seasonal naive baseline in the backtest, "
            "so the baseline is used."
        )

    fc = _final_forecast(chosen_cand, prep, horizon, seed, fold_cache, cuts, bh)
    if fc is None:
        warnings.append(f"{chosen.name} failed on the full history; the baseline was used instead.")
        chosen, chosen_cand, no_better = baseline, cands[0], True
        fc = stat_forecast(cands[0], prep.train, prep.index, freq, horizon)
    fc = fc.clipped(0.0 if prep.non_negative else None)

    final_level, reasons = derive_confidence(
        mase=chosen.mase if np.isfinite(chosen.mase) else None,
        beats_baseline=not no_better,
        cycles=prep.cycles,
        coverage80=chosen.coverage80,
    )
    if limited and stop_reason is not None:
        final_level = "low"
        reasons = [
            "only the simple baseline could be run because "
            + reason_text(stop_reason, budget)
            + ", so no better model could be looked for"
        ]
    method = (
        f"{chosen.name} chosen by rolling-origin backtesting ({windows} windows of {bh} "
        f"{freq.plural}, expanding training window) on {n} {freq.plural} of net history"
    )
    return ForecastResult(
        **base_kwargs,
        value=round(float(fc.point[0]), 2),
        confidence=final_level,
        method=method,
        warnings=warnings,
        model=chosen.name,
        point=_floats(fc.point),
        lo80=_floats(fc.lo80),
        hi80=_floats(fc.hi80),
        lo95=_floats(fc.lo95),
        hi95=_floats(fc.hi95),
        scores=scores,
        skipped=skipped,
        baseline_score=baseline,
        winner_score=chosen,
        no_better_than_baseline=no_better,
        confidence_reasons=reasons,
        backtest_horizon=bh,
        backtest_windows=windows,
    )


def _final_forecast(
    cand: Candidate,
    prep: PreparedSeries,
    horizon: int,
    seed: int,
    folds: dict[str, list[Forecast]],
    cuts: list[int],
    bh: int,
) -> Forecast | None:
    try:
        if cand.kind == "lgbm":
            pool = lgbm_error_pool(folds[cand.name], prep.train, cuts, bh)
            fc = lgbm_forecast(prep.train, prep.index, prep.freq, horizon, seed, pool)
        else:
            fc = stat_forecast(cand, prep.train, prep.index, prep.freq, horizon)
    except Exception:
        return None
    return fc if fc.finite() else None


def _fallback(
    prep: PreparedSeries,
    horizon: int,
    cand: Candidate,
    base_kwargs: dict[str, Any],
    warnings: list[str],
    skipped: list[str],
) -> ForecastResult:
    """Forecast without a backtest (history too short): baseline model, low confidence."""
    try:
        fc = stat_forecast(cand, prep.train, prep.index, prep.freq, horizon)
        if not fc.finite():
            raise ValueError("non-finite")
    except Exception:
        fc = _plain_naive(prep.train, horizon)
        warnings.append(
            "The model library could not fit this short series; the last value was repeated."
        )
        cand = Candidate("Naive", "stat")
    fc = fc.clipped(0.0 if prep.non_negative else None)
    return ForecastResult(
        **base_kwargs,
        value=round(float(fc.point[0]), 2),
        confidence="low",
        method=f"{cand.name} baseline only (too little history to backtest)",
        warnings=warnings,
        model=cand.name,
        point=_floats(fc.point),
        lo80=_floats(fc.lo80),
        hi80=_floats(fc.hi80),
        lo95=_floats(fc.lo95),
        hi95=_floats(fc.hi95),
        skipped=skipped,
        no_better_than_baseline=False,
        confidence_reasons=["there was too little history to test the forecast on past data"],
    )


def _plain_naive(y: np.ndarray, h: int) -> Forecast:
    sigma = float(np.std(np.diff(y))) if len(y) > 1 else 0.0
    point = np.full(h, float(y[-1]))
    grow = sigma * np.sqrt(np.arange(1, h + 1))
    return Forecast(
        point, point - Z80 * grow, point + Z80 * grow, point - Z95 * grow, point + Z95 * grow
    )


def _future_periods(prep: PreparedSeries, horizon: int) -> list[date]:
    out: list[date] = []
    current = prep.last_period
    for _ in range(horizon):
        current = next_period(current, prep.freq)
        out.append(current)
    return out


def _floats(values: np.ndarray) -> list[float]:
    return [round(float(v), 2) for v in values]
