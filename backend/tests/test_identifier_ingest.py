"""Identifier-like columns stay text during ingestion (no lost leading zeros)."""

from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from app.services import ingest
from app.services.ingest import ingest_file, is_identifier_name, leading_zero_columns
from tests.dataset_helpers import csv_bytes, xlsx_bytes


def _convert(tmp_path: Path, content: bytes, ext: str) -> dict[str, Any]:
    src = tmp_path / f"in.{ext}"
    src.write_bytes(content)
    dst = tmp_path / "out.parquet"
    ingest_file(src, dst, ext, max_rows=100_000)
    table = pq.read_table(dst)
    return {f.name: (str(f.type), table.column(f.name).to_pylist()) for f in table.schema}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("order_id", True),
        ("id", True),
        ("zip", True),
        ("phone_number", True),
        ("sku", True),
        ("product_code", True),
        ("رقم_الطلب", True),
        ("paid", False),
        ("discount", False),
        ("nodes", False),
        ("quantity", False),
        ("revenue", False),
    ],
)
def test_identifier_name_rule(name: str, expected: bool) -> None:
    assert is_identifier_name(name) is expected


def test_leading_zero_columns_detects_only_zero_padded_values() -> None:
    rows = [["00123", "5", "0.5"], ["00456", "7", "0"]]
    assert leading_zero_columns(rows, 3) == {0}


def test_csv_identifier_names_stay_text(tmp_path: Path) -> None:
    content = csv_bytes(
        [[1001, "00123", 94107, 5], [1002, "00456", 10001, 7]],
        ["Order ID", "phone", "zip", "qty"],
    )
    cols = _convert(tmp_path, content, "csv")
    assert cols["order_id"] == ("string", ["1001", "1002"])
    assert cols["phone"] == ("string", ["00123", "00456"])
    assert cols["zip"] == ("string", ["94107", "10001"])
    assert cols["qty"][0] == "int64"


def test_csv_leading_zero_values_stay_text_without_an_identifier_name(tmp_path: Path) -> None:
    content = csv_bytes([["007", 1], ["042", 2]], ["ref", "n"])
    cols = _convert(tmp_path, content, "csv")
    assert cols["ref"] == ("string", ["007", "042"])


def test_csv_look_alike_names_are_not_identifiers(tmp_path: Path) -> None:
    content = csv_bytes([[10, 1], [20, 2]], ["paid", "discount"])
    cols = _convert(tmp_path, content, "csv")
    assert cols["paid"][0] == "int64"
    assert cols["discount"][0] == "int64"


def test_csv_arabic_identifier_name_stays_text(tmp_path: Path) -> None:
    content = csv_bytes([[1, 5], [2, 6]], ["رقم_الطلب", "الكمية"])
    cols = _convert(tmp_path, content, "csv")
    assert cols["رقم_الطلب"][0] == "string"
    assert cols["الكمية"][0] == "int64"


def test_csv_fallback_keeps_identifier_text_and_does_not_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingest, "CSV_BLOCK_BYTES", 64 * 1024)
    rows: list[list[object]] = [[i, f"{i:05d}", i] for i in range(20_000)]
    rows[15_000][2] = '"$1,200"'  # forces the per-column fallback
    src = tmp_path / "in.csv"
    src.write_bytes(csv_bytes(rows, ["order_id", "code", "amount"]))
    dst = tmp_path / "out.parquet"
    result = ingest_file(src, dst, "csv", max_rows=100_000)
    table = pq.read_table(dst)
    assert str(table.schema.field("order_id").type) == "string"
    assert table.column("code")[0].as_py() == "00000"
    assert str(table.schema.field("amount").type) == "string"
    assert result.warnings == [
        "Column 'amount' mixes numbers or dates with other text, so it was stored as text."
    ]


def test_csv_fallback_detects_zeros_that_appear_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingest, "CSV_BLOCK_BYTES", 64 * 1024)
    rows: list[list[object]] = [[i, i] for i in range(20_000)]
    rows[19_000][0] = "00077"
    rows[10_000][1] = "oops"  # forces the fallback
    src = tmp_path / "in.csv"
    src.write_bytes(csv_bytes(rows, ["ref", "val"]))
    dst = tmp_path / "out.parquet"
    result = ingest_file(src, dst, "csv", max_rows=100_000)
    table = pq.read_table(dst)
    assert str(table.schema.field("ref").type) == "string"
    assert table.column("ref")[19_000].as_py() == "00077"
    assert all("'ref'" not in w for w in result.warnings)


def test_xlsx_identifier_columns_stay_text(tmp_path: Path) -> None:
    content = xlsx_bytes(
        {"Sheet1": [["Order ID", "zip", "qty"], [1001, 94107.0, 5], [1002, "00123", 7]]}
    )
    cols = _convert(tmp_path, content, "xlsx")
    assert cols["order_id"] == ("string", ["1001", "1002"])
    assert cols["zip"] == ("string", ["94107", "00123"])
    assert cols["qty"][0] == "int64"


def test_xlsx_leading_zero_strings_in_a_plain_column_stay_text(tmp_path: Path) -> None:
    content = xlsx_bytes({"Sheet1": [["ref", "n"], ["00123", 1], [456, 2]]})
    cols = _convert(tmp_path, content, "xlsx")
    assert cols["ref"] == ("string", ["00123", "456"])
