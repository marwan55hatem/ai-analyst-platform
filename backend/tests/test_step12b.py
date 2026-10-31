"""Fixes from manual testing: outlier bounds, the duplicate cap, anomaly precision and CSV cells."""

import csv
import io
from decimal import Decimal
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app.ml.anomaly import detect
from app.ml.quality import DUPLICATE_CAP_SHARE_PCT, QualityContext
from app.services.exports import _to_csv, safe_cell
from app.services.profiling.compute import ProfileResult, compute_profile
from app.services.profiling.results import ColumnResult
from tests.ground_truth_fixtures import analyze, make
from tests.helpers import API, register

SEED = 7


def _profile(tmp_path: Path, columns: dict[str, np.ndarray]) -> ProfileResult:
    path = tmp_path / "t.parquet"
    pq.write_table(pa.table({k: pa.array(v) for k, v in columns.items()}), path)
    return compute_profile(path)


def _col(result: ProfileResult, name: str) -> ColumnResult:
    return next(c for c in result.columns if c.name == name)


def _catalog(rows: int, refund_share: float = 0.012) -> dict[str, np.ndarray]:
    """Sales across cheap and dear products: prices differ 200x and quantities are small."""
    rng = np.random.default_rng(SEED)
    levels = np.array([2.5, 8.0, 15.0, 40.0, 150.0, 500.0])
    weights = np.array([0.3, 0.25, 0.2, 0.15, 0.07, 0.03])
    price = rng.choice(levels, rows, p=weights) * rng.lognormal(0.0, 0.08, rows)
    quantity = rng.integers(1, 6, rows).astype(float)
    revenue = price * quantity
    refunds = rng.random(rows) < refund_share
    revenue[refunds] *= -1
    return {
        "unit_price": price,
        "quantity": quantity,
        "revenue": revenue,
        "cost": revenue * 0.7,
    }


# ---- outliers -----------------------------------------------------------------------------


def test_heavy_tailed_money_across_products_is_not_flagged(tmp_path: Path) -> None:
    rows = 20_000
    catalog = _catalog(rows)
    result = _profile(tmp_path, catalog)
    for name in ("unit_price", "revenue", "cost"):
        stats = _col(result, name).stats
        assert stats["outlier_bounds"]["scale"] == "log", name
        assert stats["outlier_bounds"]["lower"] > 0, name
        assert stats["outlier_count"] / rows < 0.02, (name, stats["outlier_count"])


def test_returns_are_not_outliers_and_a_typing_error_is(tmp_path: Path) -> None:
    catalog = _catalog(5_000)
    refunds = int((catalog["revenue"] < 0).sum())
    assert refunds > 0
    catalog["unit_price"][0] = 5_000_000.0
    result = _profile(tmp_path, catalog)
    assert _col(result, "unit_price").stats["outlier_count"] >= 1
    stats = _col(result, "revenue").stats
    bounds = stats["outlier_bounds"]
    values = catalog["revenue"]
    positive = values[values > 0]
    assert stats["outlier_count"] == int(
        ((positive < bounds["lower"]) | (positive > bounds["upper"])).sum()
    )


def test_money_keeps_a_positive_lower_bound_with_many_refunds(tmp_path: Path) -> None:
    result = _profile(tmp_path, _catalog(5_000, refund_share=0.3))
    bounds = _col(result, "revenue").stats["outlier_bounds"]
    assert bounds["lower"] > 0 and bounds["scale"] == "log"


def test_a_measure_with_real_negatives_stays_on_the_linear_scale(tmp_path: Path) -> None:
    rng = np.random.default_rng(SEED)
    result = _profile(tmp_path, {"temperature": rng.normal(5.0, 8.0, 2_000)})
    bounds = _col(result, "temperature").stats["outlier_bounds"]
    assert bounds["scale"] == "linear" and bounds["lower"] < 0


def test_a_tight_price_list_keeps_its_linear_bounds(tmp_path: Path) -> None:
    rng = np.random.default_rng(SEED)
    result = _profile(tmp_path, {"unit_price": rng.normal(100.0, 2.0, 2_000)})
    bounds = _col(result, "unit_price").stats["outlier_bounds"]
    assert bounds["scale"] == "linear" and bounds["lower"] > 0


# ---- duplicates ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("duplicates", "rows", "cap"),
    [
        (1, 309_254, None),
        (5, 1_000, None),  # exactly the threshold is still allowed
        (6, 1_000, "medium"),
        (3, 0, "medium"),  # an unknown size stays cautious
    ],
)
def test_duplicates_cap_confidence_only_above_the_threshold(
    duplicates: int, rows: int, cap: str | None
) -> None:
    assert DUPLICATE_CAP_SHARE_PCT == 0.5
    found = QualityContext(row_count=rows, duplicate_rows=duplicates).caveats([])
    assert found.cap == cap
    assert found.warnings  # always reported, capped or not


def test_one_duplicate_reads_as_one_row() -> None:
    assert QualityContext(row_count=10, duplicate_rows=1).caveats([]).warnings == (
        "1 exact duplicate row is included in these figures.",
    )


@pytest.mark.slow
def test_one_file_shows_one_confidence_and_attention_types_can_be_combined(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    head, *lines = content.decode().strip().split("\n")
    dirty = "\n".join([head, *lines, lines[0]]).encode()  # one duplicate row
    dataset_id, run, insights = analyze(client, owner, dirty)
    series = client.get(f"{API}/datasets/{dataset_id}/timeseries", headers=owner.headers).json()
    summary = next(i for i in insights if i["type"] == "kpi_summary")
    pareto = next(i for i in insights if i["type"] == "pareto")
    assert series["confidence"] == summary["confidence"] == pareto["confidence"] == "high"
    assert any("1 exact duplicate row is" in w for w in series["warnings"])

    picked = client.get(
        f"{API}/analysis-runs/{run['id']}/insights",
        params={"types": ["dead_stock", "churn_risk", "anomaly"]},
        headers=owner.headers,
    ).json()
    assert picked and {i["type"] for i in picked} <= {"dead_stock", "churn_risk", "anomaly"}


# ---- anomalies ----------------------------------------------------------------------------


def _busy_days(days: int, seed: int) -> np.ndarray:
    """Daily sales: trend, a yearly wave, a weekday pattern and heavy-tailed noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(days)
    level = 50_000 * (1 + t / days) * (1 + 0.3 * np.sin(2 * np.pi * t / 365))
    weekday = np.array([0.9, 0.85, 0.9, 1.0, 1.1, 1.3, 1.2])[t % 7]
    noise = np.exp(0.12 * rng.standard_t(6, days))
    return np.asarray(level * weekday * noise)


def test_heavy_tailed_ordinary_days_are_not_called_anomalies() -> None:
    values = _busy_days(1369, 3)
    plan = detect.plan_for("day", len(values))
    assert plan is not None
    scored = detect.score(values, plan, {"orders": values / 500}, SEED)
    assert len(detect.events(scored)) <= 1  # 14 with z alone at 3.5


def test_planted_days_stand_out_and_rank_by_severity() -> None:
    values = _busy_days(1369, 4)
    values[300] *= 6
    values[700] *= 0.08
    values[1100] *= 6.5
    plan = detect.plan_for("day", len(values))
    assert plan is not None
    found = detect.events(detect.score(values, plan, {"orders": values / 500}, SEED))
    assert {300, 700, 1100} <= {e.start for e in found}
    scores = [e.rank_score for e in found]
    assert scores == sorted(scores, reverse=True)


def _scored(actual: float, expected: float, z: float, min_ratio: float) -> detect.Scored:
    return detect.Scored(
        actual=np.array([actual]),
        expected=np.array([expected]),
        z=np.array([z]),
        forest=np.array([True]),
        scale="log",
        method="test",
        min_ratio=min_ratio,
    )


@pytest.mark.parametrize(
    ("actual", "expected", "z", "flagged"),
    [
        (190.0, 100.0, 5.5, False),  # a busy shop's noisy day: high z, under twice normal
        (43.0, 100.0, 5.5, False),
        (1200.0, 100.0, 4.8, True),  # a small shop's real spike: modest z, 12x normal
        (5.0, 100.0, 4.8, True),
        (1200.0, 100.0, 3.9, False),  # large but within this business's noise
    ],
)
def test_a_day_needs_a_high_z_and_a_big_effect(
    actual: float, expected: float, z: float, flagged: bool
) -> None:
    assert bool(_scored(actual, expected, z, detect.DAY_MIN_RATIO).flagged[0]) is flagged


def test_weeks_and_months_are_judged_on_z_alone() -> None:
    values = _busy_days(120, 5)
    plan = detect.plan_for("week", len(values))
    assert plan is not None
    assert detect.score(values, plan, {}, SEED).min_ratio == 1.0
    assert bool(_scored(190.0, 100.0, 5.5, 1.0).flagged[0])


# ---- CSV cells ----------------------------------------------------------------------------


@pytest.mark.parametrize("value", [-1230.50, Decimal("-1230.50"), -7, 0])
def test_numeric_cells_are_never_escaped(value: float | Decimal | int) -> None:
    assert safe_cell(value) == value


def test_negative_numbers_reach_the_file_unchanged_and_formulas_do_not() -> None:
    text = _to_csv(["key", "monetary"], [["=cmd()", Decimal("-1230.50")], ["C-1", -1230.5]])
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[1] == ["'=cmd()", "-1230.50"]
    assert rows[2] == ["C-1", "-1230.5"]
