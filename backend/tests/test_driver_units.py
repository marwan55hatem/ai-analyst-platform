"""Revenue drivers without the database: planted driver, honest wording, confidence cap, evidence."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml.base import DataFacts, ModuleSkipped
from app.ml.drivers import model as driver_model
from app.ml.modules.drivers import ASSOCIATION_NOTE, DriverModule
from app.services.query import validate_sql
from tests.ml_context import generate, module_context, read_rows, write_csv


@pytest.mark.parametrize(("seed", "years"), [(11, 2), (5, 2), (11, 3)])
def test_planted_discount_is_among_the_top_two_drivers(
    tmp_path: Path, seed: int, years: int
) -> None:
    csv_path, facts = generate(tmp_path, seed=seed, years=years, drivers=True)
    assert facts["planted"]["drivers"]["driver"] == "discount"
    with module_context(csv_path) as ctx:
        (insight,) = DriverModule().run(ctx)
    ranking = [i["factor"] for i in insight.payload["importance"]]
    assert "discount" in ranking[:2]
    discount = insight.payload["partial_dependence"]["discount"]
    by_rate = {p["value"]: 1 + p["relative_revenue_pct"] / 100 for p in discount}
    lift = 100 * (by_rate[0.3] / by_rate[0.0] - 1)
    assert 35 <= lift <= 75  # planted: units x (1 + 4 x 0.3), revenue x 0.7 after discount: +54%
    assert "lines at discount 30% average about" in insight.summary
    assert insight.result.confidence in ("medium", "low")
    assert ASSOCIATION_NOTE in insight.summary and "not proven cause" in insight.summary
    assert "association" in insight.summary.lower()
    assert insight.payload["association_not_causation"] is True
    for query in insight.evidence["queries"]:
        validate_sql(query["sql"])
        assert query["row_count"] > 0


def test_factors_without_a_clear_link_are_named_as_such(tmp_path: Path) -> None:
    csv_path, _ = generate(tmp_path, drivers=True)
    with module_context(csv_path) as ctx:
        (insight,) = DriverModule().run(ctx)
    unclear = [i["label"] for i in insight.payload["importance"] if not i["clear_link"]]
    assert "region" in unclear
    assert "No clear link on later lines for:" in insight.summary
    assert "region average about" not in insight.summary
    assert "part of its link is mechanical" in insight.summary


def test_confidence_is_never_above_medium_even_for_a_strong_fit(tmp_path: Path) -> None:
    csv_path, _ = generate(tmp_path, years=3, drivers=True)
    with module_context(csv_path) as ctx:
        (insight,) = DriverModule().run(ctx)
    assert insight.payload["r2_holdout"] > 0.5
    assert insight.result.confidence == "medium"


def test_weak_fit_or_disagreement_lowers_confidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path, _ = generate(tmp_path, drivers=True)
    real = driver_model.fit

    def weak(*args: object, **kwargs: object) -> driver_model.DriverResult:
        result = real(*args, **kwargs)  # type: ignore[arg-type]
        result.r2_holdout = 0.02
        result.permutation = {f: -v for f, v in result.permutation.items()}
        return result

    monkeypatch.setattr(driver_model, "fit", weak)
    with module_context(csv_path) as ctx:
        (insight,) = DriverModule().run(ctx)
    assert insight.result.confidence == "low"
    assert "explain little" in insight.summary
    assert "so the order is uncertain" in insight.summary


def test_sampled_rows_are_said(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    csv_path, _ = generate(tmp_path, drivers=True)
    with module_context(csv_path) as ctx:
        monkeypatch.setattr(ctx.settings, "driver_max_rows", 2000)
        (insight,) = DriverModule().run(ctx)
    assert insight.result.sample_size == 2000
    assert any("DRIVER_MAX_ROWS" in w for w in insight.result.warnings)


def test_skips_explain_themselves(tmp_path: Path) -> None:
    module = DriverModule()
    assert module.data_requirement(DataFacts(row_count=300)) == (
        "only 300 rows; revenue drivers need at least 500"
    )
    assert module.data_requirement(DataFacts(row_count=600)) is None
    assert module.requires_any == frozenset({"price", "discount", "category", "region", "channel"})
    csv_path, _ = generate(tmp_path)
    rows = read_rows(csv_path)[:400]
    small = write_csv(tmp_path / "small.csv", rows)
    with (
        module_context(small) as ctx,
        pytest.raises(ModuleSkipped, match="only 400 positive lines"),
    ):
        module.run(ctx)
    one_day = write_csv(
        tmp_path / "one_day.csv",
        [{**r, "order_date": "2024-01-01"} for r in read_rows(csv_path)[:800]],
    )
    with module_context(one_day) as ctx, pytest.raises(ModuleSkipped, match="later period"):
        module.run(ctx)


def test_partial_dependence_follows_a_known_effect() -> None:
    rng = np.random.default_rng(1)
    x = pd.DataFrame(
        {
            "discount": rng.choice([0.0, 0.1, 0.2, 0.3], size=3000),
            "region": pd.Series(rng.choice(["a", "b"], size=3000), dtype="category"),
        }
    )
    y = 2.0 * x["discount"].to_numpy() + rng.normal(scale=0.05, size=3000)
    holdout = np.arange(3000) >= 2400
    result = driver_model.fit(x, y, holdout, 42, 500)
    assert result.ranking()[0] == "discount" and result.agrees()
    points = {p["value"]: p["relative_revenue_pct"] for p in result.partial_dependence["discount"]}
    ratio = (1 + points[0.3] / 100) / (1 + points[0.0] / 100)
    assert ratio == pytest.approx(np.exp(0.6), rel=0.05)
    assert len(result.partial_dependence["region"]) == 2
