"""Unit tests of the analysis framework pieces that need no database."""

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import UnprocessableError
from app.ml import capabilities, registry
from app.ml.base import ModuleResult
from app.ml.format import fmt_money, fmt_number, fmt_pct
from app.ml.impact import impact_score
from app.ml.modules.common import (
    is_complete_period,
    jsonable,
    pct_change,
    period_unit,
    series_confidence,
    shift_period,
)
from app.ml.roles import RoleMap


def profile(name: str, role: str, original: str | None = None, **extra: Any) -> SimpleNamespace:
    """A stand-in for a ColumnProfile row."""
    return SimpleNamespace(
        name=name,
        original_name=original or name,
        semantic_role=role,
        role_confidence=extra.pop("confidence", 0.9),
        inferred_type=extra.pop("inferred_type", "datetime" if role == "date" else "float"),
        stats=extra,
    )


def test_primary_date_prefers_business_names_then_first_column() -> None:
    cols = [
        profile("ship_date", "date"),
        profile("created_at", "date"),
        profile("amount", "revenue"),
    ]
    order = ["ship_date", "created_at", "amount"]
    assert RoleMap.from_profiles(cols, order).col("date") == "created_at"
    plain = [profile("b_date", "date"), profile("a_date", "date")]
    assert RoleMap.from_profiles(plain, ["b_date", "a_date"]).col("date") == "b_date"
    arabic = [profile("c1", "date", "تاريخ_الشحن"), profile("c2", "date", "تاريخ_الطلب")]
    assert RoleMap.from_profiles(arabic, ["c1", "c2"]).col("date") == "c2"


def test_date_override_must_be_a_date_column() -> None:
    cols = [profile("d1", "date"), profile("n", "revenue")]
    assert RoleMap.from_profiles(cols, ["d1", "n"], "d1").col("date") == "d1"
    with pytest.raises(UnprocessableError):
        RoleMap.from_profiles(cols, ["d1", "n"], "n")
    with pytest.raises(UnprocessableError):
        RoleMap.from_profiles(cols, ["d1", "n"], "missing")


def test_revenue_is_derived_from_quantity_and_price() -> None:
    cols = [profile("qty", "quantity"), profile("p", "price")]
    roles = RoleMap.from_profiles(cols, ["qty", "p"])
    assert roles.derived_revenue and roles.has("revenue")
    assert roles.revenue_expr() == '("qty" * "p")'
    assert roles.revenue_expr("x") == '(x."qty" * x."p")'
    assert roles.revenue_columns() == ["qty", "p"]
    real = RoleMap.from_profiles([*cols, profile("rev", "revenue")], ["qty", "p", "rev"])
    assert not real.derived_revenue and real.revenue_expr() == '"rev"'
    none = RoleMap.from_profiles([profile("qty", "quantity")], ["qty"])
    assert not none.has("revenue") and none.revenue_expr() is None


def test_best_confidence_wins_when_a_role_repeats() -> None:
    cols = [profile("a", "revenue", confidence=0.6), profile("b", "revenue", confidence=0.95)]
    assert RoleMap.from_profiles(cols, ["a", "b"]).col("revenue") == "b"


def test_capability_reasons_are_plain_language() -> None:
    roles = RoleMap.from_profiles([profile("d", "date")], ["d"])
    caps = {c.name: c for c in capabilities.detect(registry.all_modules(), roles)}
    assert (
        caps["cohort_retention"].reason == "no customer_id column, so cohort retention is skipped"
    )
    assert "no revenue column" in str(caps["kpis"].reason)
    assert not any(c.available for c in caps.values())
    full = RoleMap.from_profiles(
        [profile("d", "date"), profile("r", "revenue"), profile("c", "customer_id")],
        ["d", "r", "c"],
    )
    caps = {c.name: c for c in capabilities.detect(registry.all_modules(), full)}
    assert all(c.available for c in caps.values())
    assert caps["pareto"].notes == ["no product column, so dead stock is skipped"]
    no_date = RoleMap.from_profiles([profile("r", "revenue"), profile("p", "product")], ["r", "p"])
    assert registry.all_modules()[2].notes(no_date) == ["no date column, so dead stock is skipped"]


def test_registry_register_and_unregister() -> None:
    before = len(registry.all_modules())
    cls = type(registry.all_modules()[0])
    assert registry.register(cls) is cls and len(registry.all_modules()) == before
    registry.unregister(cls)
    assert len(registry.all_modules()) == before - 1
    registry.register(cls)
    registry.unregister(type("Missing", (), {}))  # type: ignore[arg-type]


def test_impact_score_rules() -> None:
    assert impact_score(250, 1000, "high") == 100.0
    assert impact_score(-500, 1000, "high") == 100.0  # saturates, sign ignored
    assert impact_score(125, 1000, "high") == 50.0
    assert impact_score(125, 1000, "medium") == 30.0
    assert impact_score(125, 1000, "low") == 15.0
    assert impact_score(100, 0, "high") == 0.0
    assert impact_score(0, 1000, "high") == 0.0


def test_number_formatting_is_locale_aware() -> None:
    assert fmt_money(1234567.891, "USD") == "USD 1,234,567.89"
    assert fmt_money(1234.5) == "1,234.50"
    assert fmt_money(1234.5, "EUR", "de") == "EUR 1.234,50"
    assert fmt_money(1234.5, "EGP", "ar") == "١٬٢٣٤٫٥٠ EGP"
    assert fmt_money(-0.001) == "0.00"
    assert fmt_number(12345, "fr") == "12 345"
    assert fmt_number(2.25, "en", 1) == "2.2"
    assert fmt_pct(12.345, "en") == "12.3%"
    assert fmt_pct(-3, "de") == "-3,0%"
    assert fmt_number(5, "xx") == "5"  # unknown locale falls back to English


def test_period_helpers() -> None:
    assert period_unit("quarterly") == ("quarter", 4)
    assert period_unit("yearly") == ("year", 1)
    assert period_unit("daily") == ("month", 12) and period_unit(None) == ("month", 12)
    assert is_complete_period(date(2024, 2, 29), "month")
    assert not is_complete_period(date(2024, 2, 28), "month")
    assert is_complete_period(date(2024, 6, 30), "quarter")
    assert not is_complete_period(date(2024, 5, 31), "quarter")
    assert is_complete_period(date(2024, 12, 31), "year")
    assert not is_complete_period(date(2024, 11, 30), "year")
    assert shift_period(date(2024, 1, 1), "month", -1) == date(2023, 12, 1)
    assert shift_period(date(2024, 1, 1), "quarter", 1) == date(2024, 4, 1)
    assert shift_period(date(2024, 1, 1), "year", -1) == date(2023, 1, 1)
    assert pct_change(110, 100) == pytest.approx(10.0)
    assert pct_change(5, 0) is None
    assert series_confidence(3, 5000) == "low"
    assert series_confidence(8, 5000) == "medium"
    assert series_confidence(24, 5000) == "high"
    assert series_confidence(24, 50) == "low"


def test_jsonable_converts_exotic_values() -> None:
    from datetime import datetime
    from decimal import Decimal

    out = jsonable(
        {
            "a": Decimal("1.50"),
            "d": date(2024, 1, 2),
            "t": datetime(2024, 1, 2, 3),
            "n": float("nan"),
            "l": (1, [2]),
        }
    )
    assert out == {"a": 1.5, "d": "2024-01-02", "t": "2024-01-02", "n": None, "l": [1, [2]]}


def test_module_result_is_immutable() -> None:
    result = ModuleResult(value=1, confidence="high", sample_size=10, method="m")
    assert result.warnings == ()
    with pytest.raises(AttributeError):
        result.value = 2  # type: ignore[misc]
