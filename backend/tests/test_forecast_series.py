"""Series preparation and accuracy metrics, checked against hand calculations."""

from datetime import date

import numpy as np
import pytest

from app.ml.forecasting import metrics
from app.ml.forecasting.series import (
    DAY,
    MONTH,
    QUARTER,
    WEEK,
    cap_outliers,
    choose_frequency,
    history_sql,
    is_complete,
    next_period,
    period_label,
    period_start,
    prepare_series,
)
from app.services.query import validate_sql
from tests.forecast_helpers import monthly_values, rows_for


def test_a_series_ending_mid_month_drops_the_incomplete_month_and_says_so() -> None:
    rows = rows_for(np.arange(1000.0, 1010.0))  # Jan..Oct 2020
    prep = prepare_series("Total", rows, MONTH, last_data_day=date(2020, 10, 14))
    assert prep is not None
    assert prep.n == 9 and prep.last_period == date(2020, 9, 1)
    assert any("part-way through Oct 2020" in w and "left out" in w for w in prep.warnings)


def test_a_series_ending_on_the_last_day_keeps_every_month() -> None:
    rows = rows_for(np.arange(1000.0, 1010.0))
    prep = prepare_series("Total", rows, MONTH, last_data_day=date(2020, 10, 31))
    assert prep is not None and prep.n == 10
    assert not any("part-way" in w for w in prep.warnings)


def test_incomplete_trailing_week_and_quarter_are_dropped() -> None:
    weekly = rows_for(np.arange(100.0, 120.0), WEEK, first=date(2020, 1, 6))  # Mondays
    prep = prepare_series("w", weekly, WEEK, last_data_day=date(2020, 5, 20))  # a Wednesday
    assert prep is not None and prep.n == 19
    quarterly = [
        {"period": date(2020 + i // 4, 3 * (i % 4) + 1, 1), "value": 100.0 + i, "rows": 3}
        for i in range(8)
    ]
    prep_q = prepare_series("q", quarterly, QUARTER, last_data_day=date(2021, 11, 15))
    assert prep_q is not None and prep_q.n == 7


def test_missing_periods_are_filled_with_zero_and_recorded() -> None:
    rows = rows_for(np.arange(1000.0, 1012.0))
    del rows[5:7]  # no records for two months
    prep = prepare_series("Total", rows, MONTH, last_data_day=date(2020, 12, 31))
    assert prep is not None and prep.n == 12
    assert prep.raw[5] == 0.0 and prep.raw[6] == 0.0
    assert any("2 month(s) had no records" in w and "zero" in w for w in prep.warnings)


def test_outliers_are_capped_for_training_only() -> None:
    values = monthly_values(36, seed=3)
    values[20] *= 40
    rows = rows_for(values)
    prep = prepare_series("Total", rows, MONTH, last_data_day=date(2022, 12, 31))
    assert prep is not None
    assert prep.raw[20] == pytest.approx(values[20])  # the real value is kept for display
    assert prep.train[20] < prep.raw[20] / 5  # training sees the capped one
    assert any("capped for training only" in w for w in prep.warnings)


def test_a_normal_seasonal_peak_is_not_an_outlier() -> None:
    values = monthly_values(48, seed=2, noise=0.02, amplitude=0.6)
    _, capped = cap_outliers(values, 12)
    assert capped == 0


def test_too_little_data_returns_none() -> None:
    assert prepare_series("x", rows_for(np.array([1.0, 2.0])), MONTH, date(2020, 2, 29)) is None
    assert prepare_series("x", [], MONTH, date(2020, 2, 29)) is None


@pytest.mark.parametrize(
    ("granularity", "first", "last", "expected"),
    [
        ("daily", date(2023, 1, 1), date(2024, 12, 31), MONTH),
        ("daily", date(2024, 1, 1), date(2024, 8, 1), WEEK),
        ("daily", date(2024, 1, 1), date(2024, 4, 29), DAY),
        ("weekly", date(2024, 1, 1), date(2024, 4, 29), WEEK),
        ("weekly", date(2022, 1, 1), date(2024, 4, 29), MONTH),
        ("monthly", date(2020, 1, 1), date(2024, 4, 29), MONTH),
        ("quarterly", date(2010, 1, 1), date(2024, 4, 29), QUARTER),
        ("yearly", date(2000, 1, 1), date(2024, 4, 29), None),
        (None, date(2023, 1, 1), date(2024, 12, 31), MONTH),
    ],
)
def test_frequency_choice(
    granularity: str | None, first: date, last: date, expected: object
) -> None:
    assert choose_frequency(granularity, first, last) == expected


def test_period_arithmetic_and_labels() -> None:
    assert period_start(date(2024, 5, 17), MONTH) == date(2024, 5, 1)
    assert period_start(date(2024, 5, 17), WEEK) == date(2024, 5, 13)
    assert period_start(date(2024, 5, 17), QUARTER) == date(2024, 4, 1)
    assert period_start(date(2024, 5, 17), DAY) == date(2024, 5, 17)
    assert next_period(date(2024, 12, 1), MONTH) == date(2025, 1, 1)
    assert next_period(date(2024, 10, 1), QUARTER) == date(2025, 1, 1)
    assert next_period(date(2024, 5, 13), WEEK) == date(2024, 5, 20)
    assert next_period(date(2024, 5, 13), DAY) == date(2024, 5, 14)
    assert is_complete(date(2024, 2, 29), MONTH) and not is_complete(date(2024, 2, 28), MONTH)
    assert period_label(date(2024, 3, 1), MONTH) == "Mar 2024"
    assert period_label(date(2024, 4, 1), QUARTER) == "2024 Q2"
    assert period_label(date(2024, 5, 13), WEEK) == "week of 13 May 2024"
    assert period_label(date(2024, 5, 13), DAY) == "2024-05-13"


def test_history_sql_is_valid_for_the_safe_query_service() -> None:
    plain = history_sql('"revenue"', '"order_date"', MONTH)
    grouped = history_sql('"revenue"', '"order_date"', WEEK, '"category"', ["Office", "O'Neil"])
    all_groups = history_sql('"revenue"', '"order_date"', DAY, '"category"')
    for sql in (plain, grouped, all_groups):
        validate_sql(sql)
    assert "O''Neil" in grouped


# ---- metrics -----------------------------------------------------------------------------------


def test_metrics_match_hand_calculations() -> None:
    actual = np.array([100.0, 200.0, 300.0])
    predicted = np.array([110.0, 180.0, 330.0])
    assert metrics.mae(actual, predicted) == pytest.approx((10 + 20 + 30) / 3)
    expected = 100 * np.mean([20 / 210, 40 / 380, 60 / 630])
    assert metrics.smape(actual, predicted) == pytest.approx(expected)
    assert metrics.mase(actual, predicted, scale=10.0) == pytest.approx(2.0)


def test_smape_counts_two_zeros_as_a_perfect_period() -> None:
    assert metrics.smape(np.array([0.0, 10.0]), np.array([0.0, 10.0])) == 0.0


def test_naive_scale_uses_the_season_when_there_is_more_than_a_cycle() -> None:
    history = np.array([1.0, 2.0, 3.0, 4.0, 2.0, 3.0, 4.0, 5.0])  # season 4
    assert metrics.naive_scale(history, 4) == pytest.approx(1.0)
    assert metrics.naive_scale(np.array([1.0, 3.0, 2.0]), 12) == pytest.approx(1.5)  # lag 1
    assert metrics.naive_scale(np.array([5.0, 5.0, 5.0]), 1) == 0.0


def test_mase_of_a_constant_history_is_undefined_not_a_crash() -> None:
    assert metrics.mase(np.array([1.0]), np.array([1.0]), 0.0) == 0.0
    assert metrics.mase(np.array([1.0]), np.array([2.0]), 0.0) == float("inf")


def test_coverage() -> None:
    assert metrics.coverage(
        np.array([1.0, 5.0, 9.0, 20.0]), np.zeros(4), np.array([2.0, 6.0, 10.0, 10.0])
    ) == pytest.approx(0.75)
