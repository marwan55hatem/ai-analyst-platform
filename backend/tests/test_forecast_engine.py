"""Model selection, honesty about weak results, budgets, confidence and wording."""

from datetime import date, timedelta

import numpy as np
import pytest

from app.ml.forecasting import engine as engine_module
from app.ml.forecasting.budget import Budget, reason_text
from app.ml.forecasting.confidence import derive_confidence, lower
from app.ml.forecasting.engine import (
    ForecastResult,
    ModelScore,
    forecast_series,
    plan_folds,
)
from app.ml.forecasting.models import _stat, candidate_list, lgbm_applicable
from app.ml.forecasting.series import MONTH, WEEK, prepare_series
from app.ml.forecasting.text import forecast_text, unreliable_reason
from tests.forecast_helpers import (
    last_day_of,
    monthly_values,
    prepared,
    rows_for,
)

SEED = 42


def _budget() -> Budget:
    return Budget(600, 4000, rss_provider=lambda: 100.0)


# ---- acceptance: accuracy and calibration -------------------------------------------------------


def test_chosen_model_is_accurate_and_beats_seasonal_naive_on_trend_plus_seasonality() -> None:
    coverages = []
    for seed in range(1, 7):
        result = forecast_series(prepared(monthly_values(48, seed)), 6, _budget(), SEED)
        win, base = result.winner_score, result.baseline_score
        assert win is not None and base is not None
        assert win.smape < 12.0, (seed, result.model, win.smape)
        assert not result.no_better_than_baseline
        assert win.mase < base.mase and win.smape < base.smape
        assert result.model != "SeasonalNaive"
        assert result.confidence in ("medium", "high")
        coverages.append(win.coverage80)
    # pooled over seeds the 80% range really covers between 70% and 90% of backtest outcomes
    assert 0.70 <= float(np.mean(coverages)) <= 0.90, coverages


def test_forecast_has_nested_80_and_95_intervals_and_the_right_periods() -> None:
    result = forecast_series(prepared(monthly_values(48, 1)), 6, _budget(), SEED)
    assert len(result.point) == 6 and result.periods[0] == date(2024, 1, 1)
    assert result.periods[-1] == date(2024, 6, 1)
    for i in range(6):
        assert (
            result.lo95[i] <= result.lo80[i] <= result.point[i] <= result.hi80[i] <= result.hi95[i]
        )
        assert result.lo80[i] >= 0.0  # sales cannot go negative
    assert result.value == result.point[0]
    assert result.sample_size == 480 and result.method.startswith(result.model)


def test_same_seed_gives_identical_forecasts() -> None:
    first = forecast_series(prepared(monthly_values(48, 5)), 6, _budget(), SEED)
    second = forecast_series(prepared(monthly_values(48, 5)), 6, _budget(), SEED)
    assert first.point == second.point and first.model == second.model
    assert [s.as_dict() for s in first.scores] == [s.as_dict() for s in second.scores]


# ---- honest edge cases ---------------------------------------------------------------------------


def test_a_short_series_uses_only_simple_models_and_is_low_confidence() -> None:
    result = forecast_series(prepared(monthly_values(18, 2)), 6, _budget(), SEED)
    assert result.confidence == "low"
    names = {s.name for s in result.scores}
    assert names <= {"SeasonalNaive", "Naive", "AutoETS", "AutoTheta"} and "LightGBM" not in names
    assert any("less than two full seasonal cycles" in w for w in result.warnings)
    assert len(result.point) == 6


def test_a_series_under_one_cycle_falls_back_to_naive_and_skips_the_backtest() -> None:
    result = forecast_series(prepared(monthly_values(9, 2)), 6, _budget(), SEED)
    assert result.confidence == "low" and result.model == "Naive"
    assert result.scores == [] and any("too short to test" in w for w in result.warnings)
    assert all(np.isfinite(result.point)) and all(np.isfinite(result.lo95))


def test_a_series_with_gaps_runs_and_reports_the_fill() -> None:
    rows = rows_for(monthly_values(40, 4))
    del rows[10:13]
    prep = prepare_series("Total", rows, MONTH, last_day_of(rows))
    assert prep is not None
    result = forecast_series(prep, 6, _budget(), SEED)
    assert any("had no records" in w for w in result.warnings)
    assert result.confidence in ("low", "medium", "high") and len(result.point) == 6


def test_a_constant_series_is_flat_low_confidence_and_says_why() -> None:
    result = forecast_series(prepared(np.full(30, 500.0)), 4, _budget(), SEED)
    assert result.constant and result.confidence == "low" and result.model == "Constant"
    assert result.point == [500.0] * 4 and result.lo95 == result.hi95 == [500.0] * 4
    assert any("same value" in w for w in result.warnings)


def test_a_tiny_series_still_returns_something_honest() -> None:
    result = forecast_series(prepared(np.array([100.0, 120.0, 110.0, 130.0])), 3, _budget(), SEED)
    assert result.confidence == "low" and len(result.point) == 3


def test_no_better_than_baseline_uses_the_baseline_with_low_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pure_season = 1000 + 300 * np.sin(2 * np.pi * np.arange(36) / 12)
    monkeypatch.setattr(
        engine_module, "candidate_list", lambda n, s: [_stat("SeasonalNaive", s), _stat("Naive", 1)]
    )
    result = forecast_series(prepared(pure_season), 6, _budget(), SEED)
    assert result.no_better_than_baseline and result.model == "SeasonalNaive"
    assert result.confidence == "low"
    assert any("No model was clearly better" in w for w in result.warnings)
    title, summary = forecast_text(
        result, metric="revenue", confidence="low", currency=None, locale="en"
    )
    assert summary.startswith("This forecast is unreliable: it was no better than a seasonal naive")


def test_a_model_that_fails_is_skipped_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    real = engine_module._run_backtest

    def flaky(cand, *args, **kwargs):  # type: ignore[no-untyped-def]
        if cand.name == "AutoTheta":
            raise RuntimeError("boom")
        return real(cand, *args, **kwargs)

    monkeypatch.setattr(engine_module, "_run_backtest", flaky)
    result = forecast_series(prepared(monthly_values(48, 1)), 6, _budget(), SEED)
    assert "AutoTheta" in result.skipped
    assert any("AutoTheta could not be fitted" in w for w in result.warnings)
    assert "AutoTheta" not in {s.name for s in result.scores}


# ---- fold planning and candidates ------------------------------------------------------------------


def test_fold_plan_scales_with_history() -> None:
    assert plan_folds(48, 12, 6) == (6, 3, 6)
    bh, step, windows = plan_folds(30, 12, 6) or (0, 0, 0)
    assert bh == 6 and windows >= 2 and 30 - bh - (windows - 1) * step >= 13
    assert plan_folds(10, 12, 6) is None
    short = plan_folds(16, 1, 12)
    assert short is not None and short[0] <= 4  # horizon shrinks to leave training data


def test_candidates_depend_on_history_length() -> None:
    assert [c.name for c in candidate_list(10, 12)] == ["Naive", "AutoETS", "AutoTheta"]
    assert [c.name for c in candidate_list(18, 12)] == [
        "SeasonalNaive",
        "Naive",
        "AutoETS",
        "AutoTheta",
    ]
    full = [c.name for c in candidate_list(36, 12)]
    assert full[0] == "SeasonalNaive" and "AutoARIMA" in full and full[-1] == "LightGBM"
    weekly = [c.name for c in candidate_list(120, 52)]
    assert "AutoARIMA" not in weekly and "LightGBM" in weekly
    assert lgbm_applicable(36, 12) and not lgbm_applicable(10, 12)


def test_daily_data_with_a_weekly_pattern_is_forecast_daily() -> None:
    from app.ml.forecasting.series import DAY

    t = np.arange(120)
    values = 1000 + 3 * t + 200 * np.sin(2 * np.pi * t / 7)
    values = values + np.random.default_rng(1).normal(0, 20, 120)
    result = forecast_series(prepared(values, DAY), 7, _budget(), SEED)
    assert result.freq_key == "day" and result.winner_score is not None
    assert result.winner_score.smape < 12.0 and result.periods[0] == date(2020, 1, 1) + timedelta(
        days=120
    )


def test_weekly_series_get_a_weekly_calendar() -> None:
    values = monthly_values(60, 3)
    result = forecast_series(prepared(values, WEEK), 4, _budget(), SEED)
    assert result.freq_key == "week" and result.periods[1] - result.periods[0] == timedelta(days=7)


# ---- budget: time and memory --------------------------------------------------------------------


def test_exceeding_the_memory_limit_skips_models_with_a_warning_but_still_forecasts() -> None:
    over = Budget(600, 400, rss_provider=lambda: 900.0)
    result = forecast_series(prepared(monthly_values(48, 1)), 6, over, SEED)
    assert [s.name for s in result.scores] == ["SeasonalNaive"]  # only the cheap baseline ran
    assert {"AutoETS", "AutoTheta", "AutoARIMA", "LightGBM"} <= set(result.skipped)
    assert any("memory limit of 400 MB" in w for w in result.warnings)
    # nothing was compared, so it must not claim "no better than baseline"
    assert not result.no_better_than_baseline and result.confidence == "low"
    assert "only the simple baseline could be run" in result.confidence_reasons[0]
    _, summary = forecast_text(
        result, metric="revenue", confidence="low", currency=None, locale="en"
    )
    assert summary.startswith("This forecast is unreliable: only the simple baseline")
    assert len(result.point) == 6 and over.peak_rss_mb == 900.0


def test_running_out_of_time_skips_the_heavy_models_first() -> None:
    calls = [0]

    def clock() -> float:  # every look at the clock costs 12 simulated seconds
        calls[0] += 1
        return 12.0 * (calls[0] - 1)

    budget = Budget(60, 4000, rss_provider=lambda: 10.0, clock=clock)
    result = forecast_series(prepared(monthly_values(48, 1)), 6, budget, SEED)
    assert "LightGBM" in result.skipped
    assert any("time limit of 60 seconds" in w for w in result.warnings)
    assert len(result.point) == 6


def test_budget_reports_its_reason_in_plain_words() -> None:
    budget = Budget(30, 256, rss_provider=lambda: 1.0)
    assert reason_text("memory", budget) == "the memory limit of 256 MB was reached"
    assert reason_text("time", budget) == "the time limit of 30 seconds was reached"
    assert budget.exhausted() is None and budget.elapsed() >= 0 and budget.remaining() <= 30
    budget.release()


# ---- confidence --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (dict(mase=0.5, beats_baseline=True, cycles=4.0, coverage80=0.8), "high"),
        (dict(mase=0.5, beats_baseline=True, cycles=2.5, coverage80=0.8), "medium"),
        (dict(mase=0.9, beats_baseline=True, cycles=4.0, coverage80=0.8), "medium"),
        (dict(mase=1.3, beats_baseline=True, cycles=4.0, coverage80=0.8), "low"),
        (dict(mase=0.3, beats_baseline=True, cycles=1.5, coverage80=0.8), "low"),
        (dict(mase=0.3, beats_baseline=False, cycles=4.0, coverage80=0.8), "low"),
        (dict(mase=None, beats_baseline=True, cycles=4.0, coverage80=None), "low"),
        (dict(mase=0.5, beats_baseline=True, cycles=4.0, coverage80=0.3), "medium"),
        (dict(mase=0.8, beats_baseline=True, cycles=4.0, coverage80=0.99), "low"),
    ],
)
def test_confidence_rules(kwargs: dict, expected: str) -> None:
    level, reasons = derive_confidence(**kwargs)
    assert level == expected
    if expected != "high":
        assert reasons


def test_lower_never_goes_below_low() -> None:
    assert lower("high") == "medium" and lower("medium") == "low" and lower("low") == "low"


# ---- wording: estimate + 80% range, unreliable first when weak ----------------------------------


def _result(confidence: str, *, no_better: bool = False, constant: bool = False) -> ForecastResult:
    win = ModelScore("AutoETS", 6.2, 0.55, 1234.0, 0.8, 0.95, 3)
    base = ModelScore("SeasonalNaive", 11.0, 1.1, 2300.0, 0.8, 0.95, 3)
    return ForecastResult(
        series="Total",
        value=50_000.0,
        confidence=confidence,  # type: ignore[arg-type]
        sample_size=480,
        method="AutoETS chosen by rolling-origin backtesting",
        warnings=[],
        model="AutoETS",
        periods=[date(2025, 1, 1), date(2025, 2, 1)],
        point=[50_000.0, 52_000.0],
        lo80=[45_000.0, 46_000.0],
        hi80=[55_000.0, 58_000.0],
        lo95=[42_000.0, 43_000.0],
        hi95=[58_000.0, 61_000.0],
        history_periods=[date(2024, 12, 1)],
        history=[48_000.0],
        scores=[win, base],
        baseline_score=base,
        winner_score=win,
        no_better_than_baseline=no_better,
        confidence_reasons=["fewer than two full seasonal cycles of history"],
        cycles=3.0,
        backtest_horizon=6,
        backtest_windows=3,
        constant=constant,
    )


def _text(result: ForecastResult, confidence: str) -> tuple[str, str]:
    return forecast_text(
        result, metric="revenue", confidence=confidence, currency="USD", locale="en"
    )


@pytest.mark.parametrize("confidence", ["high", "medium", "low"])
def test_every_title_and_summary_says_estimate_and_shows_the_80_percent_range(
    confidence: str,
) -> None:
    title, summary = _text(_result(confidence), confidence)
    assert title == (
        "Net revenue is estimated at about USD 50,000.00 next month "
        "(80% range: USD 45,000.00 to USD 55,000.00)"
    )
    assert "estimate" in summary and "80% range: USD 45,000.00 to USD 55,000.00" in summary
    assert "95% range: USD 42,000.00 to USD 58,000.00" in summary
    assert "Method: exponential smoothing (ETS)" in summary and "MASE" in summary
    assert "seasonal naive baseline was off by about 11.0%" in summary


def test_high_confidence_wording_has_no_warning_or_hedge() -> None:
    _, summary = _text(_result("high"), "high")
    assert "unreliable" not in summary and "guide rather than" not in summary
    assert summary.startswith("It is an estimate, not a promise")


def test_medium_confidence_is_called_a_guide_not_a_firm_number() -> None:
    _, summary = _text(_result("medium"), "medium")
    assert "unreliable" not in summary
    assert "Treat it as a guide rather than a firm number." in summary


def test_low_confidence_starts_by_saying_it_is_unreliable_and_why() -> None:
    _, summary = _text(_result("low"), "low")
    assert summary.startswith(
        "This forecast is unreliable: fewer than two full seasonal cycles of history."
    )


def test_confidence_capped_by_data_quality_is_what_the_text_reflects() -> None:
    result = _result("high")
    assert unreliable_reason(result, "high") is None
    assert unreliable_reason(result, "low") is not None


def test_a_constant_series_is_called_unreliable_with_its_own_reason() -> None:
    _, summary = _text(_result("low", constant=True), "low")
    assert summary.startswith("This forecast is unreliable: every period had the same value")


def test_group_titles_name_the_group_and_quantity_is_a_plain_count() -> None:
    title, _ = forecast_text(
        _result("high"),
        metric="quantity",
        confidence="high",
        currency=None,
        locale="en",
        group="Office",
    )
    assert title.startswith("Quantity sold for Office is estimated at about 50,000 next month")
