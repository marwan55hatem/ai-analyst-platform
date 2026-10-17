"""Column statistics and type inference, checked against independent calculations."""

from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.core.config import get_settings
from app.services.profiling.compute import ProfileResult, compute_profile
from app.services.profiling.engine import open_parquet
from app.services.profiling.results import ColumnResult

NUMBERS = [1, 2, 2, 3, 4, 7, 9, 20, 35, None]


def _write(tmp_path: Path, columns: dict[str, list[Any]]) -> Path:
    path = tmp_path / "t.parquet"
    pq.write_table(pa.table(columns), path)
    return path


def _profile(tmp_path: Path, columns: dict[str, list[Any]]) -> ProfileResult:
    return compute_profile(_write(tmp_path, columns))


def _col(result: ProfileResult, name: str) -> ColumnResult:
    return next(c for c in result.columns if c.name == name)


def test_numeric_statistics_match_numpy(tmp_path: Path) -> None:
    result = _profile(tmp_path, {"amount": pa.array(NUMBERS, pa.int64())})
    stats = _col(result, "amount").stats
    values = np.array([v for v in NUMBERS if v is not None], dtype=float)
    assert _col(result, "amount").inferred_type == "integer"
    assert stats["row_count"] == 10
    assert stats["null_count"] == 1
    assert stats["null_pct"] == 10.0
    assert stats["distinct_count"] == 8
    assert stats["min"] == 1 and stats["max"] == 35
    assert stats["mean"] == pytest.approx(values.mean())
    assert stats["median"] == pytest.approx(np.median(values))
    assert stats["std"] == pytest.approx(values.std(ddof=1))
    q = stats["quantiles"]
    for key, p in (("p25", 25), ("p50", 50), ("p75", 75), ("p95", 95)):
        assert q[key] == pytest.approx(np.percentile(values, p))


def test_skewness_is_the_adjusted_fisher_pearson_coefficient(tmp_path: Path) -> None:
    result = _profile(tmp_path, {"amount": pa.array(NUMBERS, pa.int64())})
    values = np.array([v for v in NUMBERS if v is not None], dtype=float)
    n, m = len(values), values.mean()
    g1 = ((values - m) ** 3).mean() / ((values - m) ** 2).mean() ** 1.5
    expected = g1 * np.sqrt(n * (n - 1)) / (n - 2)
    assert _col(result, "amount").stats["skewness"] == pytest.approx(expected)


def test_iqr_outliers(tmp_path: Path) -> None:
    values = list(range(10, 30)) + [500, -400]
    result = _profile(tmp_path, {"measure": pa.array(values, pa.int64())})
    stats = _col(result, "measure").stats
    q1, q3 = np.percentile(values, [25, 75])
    lower, upper = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    assert stats["outlier_count"] == sum(1 for v in values if v < lower or v > upper) == 2
    assert stats["outlier_bounds"]["upper"] == pytest.approx(upper)


def test_flag_like_columns_have_no_outliers(tmp_path: Path) -> None:
    result = _profile(tmp_path, {"flag": pa.array([0] * 50 + [1], pa.int64())})
    assert _col(result, "flag").stats["outlier_count"] == 0


def test_top_ten_values_are_sorted_by_count(tmp_path: Path) -> None:
    cities = ["a"] * 5 + ["b"] * 3 + ["c"] * 2 + [f"x{i}" for i in range(12)]
    result = _profile(tmp_path, {"city": cities})
    top = _col(result, "city").stats["top_values"]
    assert len(top) == 10
    assert top[:3] == [
        {"value": "a", "count": 5},
        {"value": "b", "count": 3},
        {"value": "c", "count": 2},
    ]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["1", "2", "3"] * 10, "integer"),
        (["1.5", "2.25", "3"] * 10, "float"),
        (["$1,200", "$2,500.50", "$300"] * 10, "float"),
        (["١٢", "٣٤", "٥٦"] * 10, "integer"),
        (["15%", "20%", "5%"] * 10, "float"),
        (["yes", "no", "yes"] * 10, "boolean"),
        (["2024-01-05", "2024-02-10", "2024-03-15"] * 10, "datetime"),
        (["5 Jan 2024", "10 Feb 2024", "15 Mar 2024"] * 10, "datetime"),
        (["north", "south", "east"] * 10, "categorical"),
        ([f"free text number {i} here" for i in range(60)], "text"),
        ([f"INV-{i:05d}" for i in range(80)], "identifier"),
    ],
)
def test_type_inference_from_text(tmp_path: Path, values: list[str], expected: str) -> None:
    result = _profile(tmp_path, {"value": values})
    assert _col(result, "value").inferred_type == expected


def test_numbers_stored_as_text_are_flagged_for_casting(tmp_path: Path) -> None:
    result = _profile(tmp_path, {"price": ["$1,200", "$99.50", "abc"] + ["$10"] * 97})
    column = _col(result, "price")
    assert column.inferred_type == "float"
    assert column.stats["stored_as"] == "text"
    assert column.stats["needs_cast"] is True
    assert column.stats["invalid_count"] == 1
    assert column.stats["conform_share"] == pytest.approx(0.99)
    assert column.stats["max"] == pytest.approx(1200.0)


def test_mixed_types_are_flagged_with_the_conforming_share(tmp_path: Path) -> None:
    values = [str(i) for i in range(30)] + [f"item {i}" for i in range(20)]
    column = _col(_profile(tmp_path, {"mix": values}), "mix")
    assert column.inferred_type == "text"
    assert column.stats["mixed_types"] is True
    assert column.stats["numeric_share"] == pytest.approx(0.6)


def test_identifier_names_and_leading_zeros_are_identifiers(tmp_path: Path) -> None:
    result = _profile(
        tmp_path,
        {
            "zip_code": ["10001", "94107", "10001"],
            "ref": ["00123", "00456", "00789"],
            "order_id": pa.array([1, 2, 3], pa.int64()),
        },
    )
    assert {c.name: c.inferred_type for c in result.columns} == {
        "zip_code": "identifier",
        "ref": "identifier",
        "order_id": "identifier",
    }
    assert _col(result, "ref").stats["leading_zeros"] is True


def test_blank_text_counts_as_null(tmp_path: Path) -> None:
    result = _profile(tmp_path, {"c": ["a", "", "  ", None, "b"]})
    stats = _col(result, "c").stats
    assert stats["null_count"] == 3
    assert stats["non_null_count"] == 2


def test_whitespace_and_case_variants_are_counted(tmp_path: Path) -> None:
    values = ["North"] * 6 + ["north"] * 2 + [" South"] * 3 + ["South"] * 4 + ["NORTH"]
    stats = _col(_profile(tmp_path, {"region": values}), "region").stats
    assert stats["whitespace_count"] == 3
    assert (
        stats["case_variant_groups"] == 1
    )  # north variants; ' South'/'South' differ by spaces only
    assert stats["case_variant_cells"] == 3


def test_constant_columns_and_duplicates_are_reported(tmp_path: Path) -> None:
    result = _profile(
        tmp_path,
        {"a": [1, 1, 2, 2, 3], "b": ["x", "x", "y", "y", "z"], "const": ["k"] * 5},
    )
    assert result.dataset["constant_columns"] == ["const"]
    assert result.dataset["duplicate_rows"] == 2
    assert result.dataset["duplicate_pct"] == 40.0


def test_high_cardinality_columns_are_listed(tmp_path: Path) -> None:
    names = [f"customer name number {i}" for i in range(120)]
    result = _profile(tmp_path, {"full_name": names, "small": ["a", "b"] * 60})
    assert result.dataset["high_cardinality_columns"] == ["full_name"]


def test_non_finite_numbers_do_not_break_json(tmp_path: Path) -> None:
    values = pa.array([1.0, float("nan"), float("inf"), 4.0, 5.0, 6.0], pa.float64())
    result = _profile(tmp_path, {"x": values})
    import json

    json.dumps(_col(result, "x").stats, allow_nan=False)


def test_profiling_uses_a_memory_limited_duckdb_view(tmp_path: Path) -> None:
    path = _write(tmp_path, {"a": [1, 2, 3]})
    with open_parquet(path) as con:
        limit = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        kind = con.execute(
            "SELECT table_type FROM information_schema.tables WHERE table_name = 'data'"
        ).fetchone()[0]
    assert get_settings().profile_memory_mb > 0
    assert "MB" in limit or "MiB" in limit or "GiB" in limit
    assert kind == "VIEW"  # streamed from Parquet, not copied into memory
