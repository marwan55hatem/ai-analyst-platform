"""What-if without the database: elasticity recovery, sanity rule, fallbacks, ranges and warnings."""

from pathlib import Path
from typing import Any

import pytest

from app.core.errors import UnprocessableError
from app.ml.whatif import elasticity
from app.ml.whatif.scenarios import Scenario, run
from app.services.query import validate_sql
from tests.ml_context import generate, module_context, read_rows, write_csv

TRUE = -1.5


def _office(ctx: Any) -> elasticity.Estimate:
    where = f"{ctx.roles.q('category')} = 'Office'"
    est, sql, _ = elasticity.fit_group(ctx, "category", "category", "Office", where)
    validate_sql(sql)
    return est


# Fixed files, the walkthrough file among them (3 years, churn, seed 11). Over 30 planted files the
# category estimate is within 0.3 of -1.5 in 29 and its 95% interval holds -1.5 in 29 (docs).
@pytest.mark.parametrize(
    ("seed", "years", "churn"), [(11, 2, False), (5, 2, False), (11, 3, True), (5, 3, False)]
)
def test_planted_elasticity_is_recovered_with_its_interval(
    tmp_path: Path, seed: int, years: int, churn: bool
) -> None:
    csv_path, facts = generate(
        tmp_path, seed=seed, years=years, elasticity=True, drivers=True, churn=churn
    )
    assert facts["planted"]["elasticity"]["true_elasticity"] == TRUE
    with module_context(csv_path) as ctx:
        est = _office(ctx)
    assert est.status == "ok" and est.grain == "week"
    assert est.elasticity is not None and est.ci_low is not None and est.ci_high is not None
    assert abs(est.elasticity - TRUE) <= 0.3
    assert est.ci_low <= TRUE <= est.ci_high
    assert est.price_range_pct is not None
    assert est.price_range_pct[0] == pytest.approx(-25, abs=3)
    assert est.price_range_pct[1] == pytest.approx(25, abs=3)


@pytest.mark.parametrize("true", [0.8, 0.0])
def test_positive_or_zero_elasticity_is_unreliable_and_never_forecast(
    tmp_path: Path, true: float
) -> None:
    csv_path, _ = generate(tmp_path, elasticity=True, true_elasticity=true)
    with module_context(csv_path) as ctx:
        est = _office(ctx)
        result = run(ctx, Scenario("price", 10, "category", "Office"))
    assert est.status == "unreliable"
    assert est.reason is not None and elasticity.UNRELIABLE_TEXT in est.reason
    assert result["status"] == "not_enough_evidence"
    assert result["revenue_change"] is None and result["profit_change"] is None
    assert "price changes in this data are not clearly linked to sales" in result["summary"]
    assert [t["scope"] for t in result["estimates_tried"]] == ["category", "all"]
    assert result["confidence"] == "low" and result["assumptions"]


def test_sanity_rule() -> None:
    assert elasticity.sanity(-1.5, -1.8, -1.2) == ("ok", None)
    status, reason = elasticity.sanity(0.4, 0.1, 0.7)
    assert status == "unreliable" and reason is not None and "positive" in reason
    status, reason = elasticity.sanity(-6.0, -7.0, -5.0)
    assert status == "unreliable" and reason is not None and "implausibly large" in reason
    status, reason = elasticity.sanity(-0.5, -1.2, 0.2)
    assert status == "unreliable" and reason is not None and "includes zero" in reason


def test_no_price_variation_is_skipped(tmp_path: Path) -> None:
    csv_path, _ = generate(tmp_path)
    with module_context(csv_path) as ctx:
        est = _office(ctx)
        result = run(ctx, Scenario("price", 5, "all"))
    assert est.status == "skipped" and "prices hardly changed" in (est.reason or "")
    assert result["status"] == "not_enough_evidence"
    assert "did not vary enough" in result["message"]


def test_price_inside_the_range_is_a_range_with_assumptions(tmp_path: Path) -> None:
    csv_path, _ = generate(tmp_path, years=3, elasticity=True, drivers=True)
    with module_context(csv_path) as ctx:
        result = run(ctx, Scenario("price", 10, "category", "Office"))
    assert result["status"] == "ok" and not result["extrapolation_risk"]
    rev, profit = result["revenue_change"], result["profit_change"]
    assert rev["low"] < rev["expected"] < rev["high"] < 0  # elastic: revenue falls
    assert profit["low"] < profit["expected"] < profit["high"]
    assert result["elasticity"]["scope"] == "category"
    assert len(result["assumptions"]) >= 4
    assert any("not a controlled price test" in a for a in result["assumptions"])
    assert "not a promise" in result["summary"]
    assert result["confidence"] == "medium"
    for query in result["evidence"]:
        validate_sql(query["sql"])


def test_price_outside_the_observed_range_warns(tmp_path: Path) -> None:
    csv_path, _ = generate(tmp_path, years=3, elasticity=True)
    with module_context(csv_path) as ctx:
        result = run(ctx, Scenario("price", 28, "category", "Office"))
    assert result["status"] == "ok" and result["extrapolation_risk"]
    assert any(w.startswith("Extrapolation risk") for w in result["warnings"])
    assert result["confidence"] == "low"
    assert "extrapolation risk" in result["summary"]


def test_product_falls_back_to_its_category_when_its_own_estimate_is_weak(
    tmp_path: Path,
) -> None:
    csv_path, _ = generate(tmp_path, elasticity=True)
    rows = read_rows(csv_path)
    fixed = next(r["unit_price"] for r in rows if r["product"] == "Item-03")
    rows = [  # Item-03 never changes price, so only its category can say how buyers react
        {**r, "unit_price": fixed} if r["product"] == "Item-03" else r for r in rows
    ]
    thin = write_csv(tmp_path / "thin.csv", rows)
    with module_context(thin) as ctx:
        result = run(ctx, Scenario("price", -10, "product", "Item-03"))
    assert result["status"] == "ok"
    assert [t["scope"] for t in result["estimates_tried"]][:2] == ["product", "category"]
    assert result["elasticity"]["scope"] == "category"
    assert any("product estimate (Item-03) was not used" in w for w in result["warnings"])


def test_pooled_estimate_says_where_prices_varied(tmp_path: Path) -> None:
    csv_path, _ = generate(tmp_path, elasticity=True)
    with module_context(csv_path) as ctx:
        result = run(ctx, Scenario("price", 5, "all"))
    assert result["elasticity"]["scope"] == "all"
    assert any("Prices varied in only 1 of the 5 groups" in w for w in result["warnings"])


def test_discount_volume_and_cost_levers(tmp_path: Path) -> None:
    csv_path, _ = generate(tmp_path, years=3, elasticity=True, drivers=True)
    with module_context(csv_path) as ctx:
        discount = run(ctx, Scenario("discount", 20, "all"))
        volume = run(ctx, Scenario("volume", 10, "all"))
        cost = run(ctx, Scenario("cost", 5, "category", "Office"))
    for result in (discount, volume, cost):
        assert result["status"] == "ok"
        assert result["revenue_change"]["low"] <= result["revenue_change"]["high"]
        assert result["profit_change"]["low"] <= result["profit_change"]["high"]
        assert result["assumptions"] and result["confidence"] in ("medium", "low")
    assert discount["elasticity"]["discount_effect"] > 0
    assert volume["revenue_change"]["expected"] == pytest.approx(
        0.1 * volume["baseline"]["yearly_revenue"], rel=1e-6
    )
    assert volume["revenue_change"]["low"] < volume["revenue_change"]["expected"]
    assert cost["revenue_change"] == {"low": 0.0, "expected": 0.0, "high": 0.0}
    assert cost["profit_change"]["expected"] == pytest.approx(
        -0.05 * cost["baseline"]["yearly_cost"], rel=1e-6
    )


def test_profit_needs_cost_and_levers_need_their_columns(tmp_path: Path) -> None:
    csv_path, _ = generate(tmp_path, elasticity=True)
    rows = [{k: v for k, v in r.items() if k != "cost"} for r in read_rows(csv_path)]
    no_cost = write_csv(tmp_path / "no_cost.csv", rows)
    with module_context(no_cost) as ctx:
        price = run(ctx, Scenario("price", 10, "category", "Office"))
        assert price["profit_change"] is None
        assert "Profit not available: the dataset has no cost column." in price["warnings"]
        with pytest.raises(UnprocessableError, match="no cost column"):
            run(ctx, Scenario("cost", 5))
        with pytest.raises(UnprocessableError, match="no discount column"):
            run(ctx, Scenario("discount", 5))


def test_requests_are_validated(tmp_path: Path) -> None:
    csv_path, _ = generate(tmp_path)
    with module_context(csv_path) as ctx:
        with pytest.raises(UnprocessableError, match="capped"):
            run(ctx, Scenario("price", 31))
        with pytest.raises(UnprocessableError, match="No product named"):
            run(ctx, Scenario("price", 5, "product", "Nope"))
        with pytest.raises(UnprocessableError, match="needs a target"):
            run(ctx, Scenario("price", 5, "category"))


def test_short_history_is_not_enough(tmp_path: Path) -> None:
    csv_path, _ = generate(tmp_path)
    rows = [r for r in read_rows(csv_path) if r["order_date"] < "2023-03-15"]
    short = write_csv(tmp_path / "short.csv", rows)
    with module_context(short) as ctx, pytest.raises(UnprocessableError, match="complete months"):
        run(ctx, Scenario("volume", 5))


def test_results_carry_method_value_and_the_evidence_sample_size(tmp_path: Path) -> None:
    csv_path, _ = generate(tmp_path, years=3, elasticity=True, drivers=True)
    with module_context(csv_path) as ctx:
        price = run(ctx, Scenario("price", 10, "category", "Office"))
        volume = run(ctx, Scenario("volume", 10, "category", "Office"))
        single = run(ctx, Scenario("volume", 10, "product", "Item-03"))
    assert price["sample_size"] == price["elasticity"]["periods"] > 100
    assert price["value"] == price["revenue_change"] and "log-log" in price["method"]
    assert volume["sample_size"] == volume["baseline"]["months"] == 12
    mix = [q for q in volume["evidence"] if "per product" in q["purpose"]]
    assert mix and mix[0]["row_count"] == 6  # the six Office products, not one category row
    assert volume["revenue_change"]["low"] < 0.9 * volume["revenue_change"]["expected"]
    assert not [q for q in single["evidence"] if "per product" in q["purpose"]]


def test_a_discount_rate_of_95_percent_or_more_is_refused(tmp_path: Path) -> None:
    csv_path, _ = generate(tmp_path, drivers=True)
    rows = [{**r, "discount_pct": "80"} for r in read_rows(csv_path)]
    deep = write_csv(tmp_path / "deep.csv", rows)
    with module_context(deep) as ctx, pytest.raises(UnprocessableError, match="would reach 104%"):
        run(ctx, Scenario("discount", 30))
