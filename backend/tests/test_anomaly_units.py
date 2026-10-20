"""Unit tests of the anomaly maths (no database): robust z, methods, events, level shifts."""

from types import SimpleNamespace

import numpy as np
import pytest

from app.ml.anomaly import detect
from app.ml.base import DataFacts
from app.ml.modules.anomalies import AnomalyModule

SEED = 42


def _daily(days: int, seed: int = 1, level: float = 1000.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    weekly = np.array([0.9, 1.0, 1.0, 1.1, 1.3, 1.5, 0.7])
    return level * weekly[np.arange(days) % 7] * rng.lognormal(0.0, 0.1, days)


def test_robust_z_uses_median_and_mad() -> None:
    values = np.array([1.0, 2.0, 3.0, 4.0, 100.0])
    z = detect.robust_z(values)
    assert z[2] == pytest.approx(0.0)
    assert z[-1] == pytest.approx(0.6745 * 97 / 1.0)


def test_robust_z_is_zero_without_spread() -> None:
    assert np.all(detect.robust_z(np.full(10, 5.0)) == 0.0)


@pytest.mark.parametrize(
    ("unit", "points", "expected"),
    [
        ("day", 40, None),
        ("day", 60, ("stl", False)),
        ("day", 120, ("stl", True)),
        ("week", 20, None),
        ("week", 30, ("rolling_median", False)),
        ("month", 100, ("rolling_median", True)),
    ],
)
def test_methods_depend_on_history(
    unit: str, points: int, expected: tuple[str, bool] | None
) -> None:
    plan = detect.plan_for(unit, points)
    assert (None if plan is None else (plan.baseline, plan.forest)) == expected


def test_a_planted_spike_and_drop_are_found_and_classified() -> None:
    values = _daily(200)
    values[60] *= 12
    values[140] *= 0.05
    plan = detect.plan_for("day", len(values))
    assert plan is not None
    scored = detect.score(values, plan, {"orders": values / 100}, SEED)
    found = detect.events(scored)
    by_start = {e.start: e for e in found}
    assert by_start[60].kind == "spike" and by_start[140].kind == "drop"
    assert by_start[60].change > 0 > by_start[140].change
    assert by_start[60].methods == ["robust_z", "isolation_forest"]
    assert len(found) <= 4  # the forest's alert budget keeps noise days out


def test_stl_only_without_enough_history_for_the_forest() -> None:
    values = _daily(70)
    values[30] *= 15
    plan = detect.plan_for("day", len(values))
    assert plan is not None and not plan.forest
    scored = detect.score(values, plan, {}, SEED)
    assert scored.forest is None
    found = detect.events(scored)
    # without the forest a big spike can drag a neighbour over the line: that is why this mode
    # is always low confidence; the planted spike still ranks first
    assert found[0].start == 30 and found[0].kind == "spike"
    assert detect.single_method_points(scored) == {"z_only": [], "forest_only": []}


def test_consecutive_flags_merge_into_one_event_ranked_by_magnitude_and_persistence() -> None:
    scored = detect.Scored(
        actual=np.array([10.0, 50.0, 60.0, 10.0, 30.0, 10.0]),
        expected=np.full(6, 10.0),
        z=np.array([0.0, 5.0, 6.0, 0.0, 4.0, 0.0]),
        forest=np.array([False, True, True, False, True, False]),
        scale="raw",
        method="test",
    )
    found = detect.events(scored)
    assert [(e.start, e.end, e.length) for e in found] == [(1, 2, 2), (4, 4, 1)]
    assert found[0].rank_score == pytest.approx(90.0 * 2)


def test_zero_heavy_series_stays_on_the_raw_scale() -> None:
    values = _daily(100)
    values[::3] = 0.0
    plan = detect.plan_for("day", 100)
    assert plan is not None
    assert detect.score(values, plan, {}, SEED).scale == "raw"


def test_weekly_series_use_a_rolling_median_baseline() -> None:
    values = 700 * np.random.default_rng(5).lognormal(0, 0.05, 40)
    values[20] *= 10
    plan = detect.plan_for("week", 40)
    assert plan is not None
    scored = detect.score(values, plan, {}, SEED)
    assert [e.start for e in detect.events(scored)] == [20]
    assert "rolling median" in scored.method


def _weeks(n: int, shift_at: int | None, factor: float = 1.5, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    season = 1 + 0.5 * np.sin(2 * np.pi * np.arange(n) / 52)
    level = np.where(np.arange(n) >= (shift_at if shift_at is not None else n), factor, 1.0)
    return 700 * season * level * rng.lognormal(0, 0.08, n)


def test_level_shift_is_found_at_the_right_week() -> None:
    shifts = detect.level_shifts(_weeks(104, 75))
    assert len(shifts) == 1
    assert abs(shifts[0].week - 75) <= 2
    assert shifts[0].kind == "level_shift_up"
    assert shifts[0].change_pct == pytest.approx(50, abs=12)


def test_a_shift_mirrored_a_year_later_is_not_reported_twice() -> None:
    shifts = detect.level_shifts(_weeks(156, 70))
    assert [s.kind for s in shifts] == ["level_shift_up"]


def test_no_level_shift_in_a_seasonal_series_or_a_short_one() -> None:
    assert detect.level_shifts(_weeks(156, None)) == []
    assert detect.level_shifts(_weeks(60, 30)) == []
    assert detect.level_shifts(np.full(104, 7.0)) == []


def test_winsorizing_caps_single_big_days_only() -> None:
    values = _daily(120)
    values[50] *= 30
    capped = detect.winsorize_daily(values)
    assert capped[50] < values[50] / 5
    assert np.allclose(np.delete(capped, 50), np.delete(values, 50), rtol=0.35)
    assert np.array_equal(detect.winsorize_daily(np.zeros(30)), np.zeros(30))


@pytest.mark.parametrize(
    ("granularity", "span", "reason"),
    [
        ("daily", 30, "only 31 days of history; anomaly detection needs at least 56 days"),
        (
            "weekly",
            100,
            "only about 14 weeks of history; anomaly detection needs at least 26 weeks",
        ),
        (
            "quarterly",
            2000,
            "the dates are quarterly; anomaly detection needs daily, weekly or monthly data",
        ),
        ("daily", 400, None),
        ("daily", None, None),
    ],
)
def test_anomaly_data_requirement(granularity: str, span: int | None, reason: str | None) -> None:
    facts = DataFacts(date_span_days=span, granularity=granularity)
    assert AnomalyModule().data_requirement(facts) == reason


def test_data_facts_come_from_profiles() -> None:
    roles = SimpleNamespace(columns={"date": "d", "customer_id": "c", "revenue": "r"})
    profiles = [
        SimpleNamespace(name="d", stats={"span_days": 99, "granularity": "daily"}, pii_flag=False),
        SimpleNamespace(name="c", stats={"distinct_count": 40}, pii_flag=True),
        SimpleNamespace(name="r", stats={}, pii_flag=False),
    ]
    facts = DataFacts.from_profiles(roles, profiles, {"row_count": 500})  # type: ignore[arg-type]
    assert facts.row_count == 500
    assert facts.distinct == {"customer_id": 40}
    assert facts.date_span_days == 99 and facts.granularity == "daily"
    assert facts.pii_roles == frozenset({"customer_id"})
