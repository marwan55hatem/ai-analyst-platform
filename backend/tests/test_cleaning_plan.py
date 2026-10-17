"""The cleaning plan's defaults: only low-risk fixes are recommended, nothing is automatic."""

from pathlib import Path

import pytest

from app.services.profiling.cleaning_plan import KEEP, build_plan
from app.services.profiling.compute import ProfileResult
from app.services.profiling.results import Fix
from tests.retail_fixtures import build_csv, profile_csv


@pytest.fixture(scope="module")
def plan(tmp_path_factory: pytest.TempPathFactory) -> list[Fix]:
    folder = tmp_path_factory.mktemp("plan")
    result: ProfileResult = profile_csv(folder, build_csv("messy"))
    return result.plan


def _by_id(plan: list[Fix]) -> dict[str, Fix]:
    return {f.id: f for f in plan}


def test_every_fix_has_the_required_fields(plan: list[Fix]) -> None:
    assert plan
    for fix in plan:
        assert fix.id and fix.description and fix.columns
        assert fix.risk in {"low", "medium", "high"}
        assert fix.rows_affected >= 0 and fix.cells_affected >= fix.rows_affected
    assert len({f.id for f in plan}) == len(plan)


def test_only_low_risk_fixes_are_recommended(plan: list[Fix]) -> None:
    recommended = [f for f in plan if f.recommended]
    assert recommended
    assert all(f.risk == "low" for f in recommended)
    assert {f.type for f in recommended} <= {"trim", "case", "cast", "parse_dates"}


def test_dedupe_is_medium_and_not_recommended(plan: list[Fix]) -> None:
    dedupe = _by_id(plan)["dedupe"]
    assert dedupe.risk == "medium" and dedupe.recommended is False
    assert dedupe.rows_affected == 150


def test_ambiguous_dates_are_high_risk_and_not_recommended(plan: list[Fix]) -> None:
    fix = _by_id(plan)["parse_dates:delivery_date"]
    assert fix.risk == "high" and fix.recommended is False
    assert fix.strategies == ["dayfirst", "monthfirst"]
    assert "day-first" in fix.description


def test_unambiguous_dates_are_low_risk(plan: list[Fix]) -> None:
    fix = _by_id(plan)["parse_dates:order_date"]
    assert fix.risk == "low" and fix.recommended is True
    assert fix.strategies is None


def test_null_strategy_defaults_to_keep(plan: list[Fix]) -> None:
    nulls = [f for f in plan if f.type == "nulls"]
    assert nulls
    for fix in nulls:
        assert fix.default_strategy == KEEP
        assert KEEP in (fix.strategies or [])
        assert fix.recommended is False and fix.risk == "medium"
    cost = _by_id(plan)["nulls:cost"]
    assert cost.strategies == [KEEP, "impute_median", "drop_rows"]
    assert cost.strategy_risk is not None and cost.strategy_risk["drop_rows"] == "high"
    region = _by_id(plan)["nulls:region"]
    assert region.strategies == [KEEP, "impute_mode", "drop_rows"]


def test_outlier_fixes_only_flag_and_are_not_recommended(plan: list[Fix]) -> None:
    flags = [f for f in plan if f.type == "flag_outliers"]
    assert flags
    for fix in flags:
        assert "Nothing is deleted" in fix.description
        assert fix.recommended is False


def test_casts_are_low_risk_when_every_value_is_readable(plan: list[Fix]) -> None:
    cast = _by_id(plan)["cast:unit_price"]
    assert cast.risk == "low" and cast.recommended
    assert cast.params["target"] == "float"
    assert _by_id(plan)["cast:qty"].params["target"] == "integer"


def test_a_cast_that_would_lose_values_is_medium_risk_and_not_recommended(tmp_path: Path) -> None:
    csv = "price\n" + "\n".join(["$10"] * 98 + ["tbd", "oops"]) + "\n"
    result = profile_csv(tmp_path, csv.encode())
    fix = next(f for f in result.plan if f.id == "cast:price")
    assert fix.risk == "medium" and fix.recommended is False
    assert fix.params["invalid_count"] == 2


def test_clean_data_needs_few_fixes(tmp_path: Path) -> None:
    result = profile_csv(tmp_path, build_csv("clean"))
    assert {f.type for f in result.plan} <= {"flag_outliers"}
    assert not any(f.recommended for f in result.plan)


def test_build_plan_is_pure(plan: list[Fix]) -> None:
    again = build_plan([], 0)
    assert again == []
