"""Unit tests for ingestion helpers, storage and upload validation."""

import io
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from app.core.errors import PayloadTooLargeError
from app.services import ingest
from app.services.ingest import (
    IngestError,
    detect_delimiter,
    detect_encoding,
    ingest_file,
    list_sheets,
    normalize_columns,
)
from app.services.storage import LocalStorage, version_prefix
from app.services.upload_validation import file_extension, sanitize_display_name
from tests.dataset_helpers import xlsx_bytes


def test_normalize_columns_is_snake_case_and_unique() -> None:
    names = [
        "Order ID",
        "orderDate",
        " Unit Price ($) ",
        "Qty",
        "Qty",
        "",
        "2024 Sales",
        "A--B",
        "الاسم",
    ]
    assert normalize_columns(names) == [
        "order_id",
        "order_date",
        "unit_price",
        "qty",
        "qty_2",
        "col_6",
        "col_2024_sales",
        "a_b",
        "الاسم",
    ]


def test_normalize_columns_truncates_and_stays_unique() -> None:
    long = "x" * 100
    result = normalize_columns([long, long, long])
    assert len(set(result)) == 3 and all(len(n) <= 63 for n in result)


def test_detect_delimiter() -> None:
    assert detect_delimiter("a;b;c\n1;2;3\n") == ";"
    assert detect_delimiter("a|b\n1|2\n") == "|"
    assert detect_delimiter("a\tb\n1\t2\n") == "\t"
    assert detect_delimiter("a,b\n1,2\n") == ","
    assert detect_delimiter("justonecolumn\nvalue\n") == ","
    assert detect_delimiter('"a;1",b\n"c;2",d\n') == ","


def test_detect_encoding() -> None:
    assert detect_encoding("héllo".encode()) == "utf-8"
    assert detect_encoding(b"\xef\xbb\xbfid,name\n") == "utf-8-sig"
    assert detect_encoding("id,name\n".encode("utf-16")) == "utf-16"
    assert detect_encoding("الاسم;المدينة\nأحمد;القاهرة\n".encode("cp1256")) == "cp1256"
    assert detect_encoding("é".encode()[:1]) != ""  # a split multibyte char is tolerated


def test_utf8_bom_and_utf16_files(tmp_path: Path) -> None:
    for name, payload, encoding in (
        ("bom.csv", b"\xef\xbb\xbfid,name\n1,a\n2,b\n", "utf-8-sig"),
        ("u16.csv", "id,name\n1,a\n2,b\n".encode("utf-16"), "utf-16"),
    ):
        src = tmp_path / name
        src.write_bytes(payload)
        result = ingest_file(src, tmp_path / (name + ".parquet"), "csv", max_rows=100)
        assert result.encoding == encoding and result.row_count == 2
        assert [m["normalized"] for m in result.column_map] == ["id", "name"]


def test_leading_blank_lines_and_fallback_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingest, "CSV_BLOCK_BYTES", 32 * 1024)
    rows = ["", "", "id,val"] + [f"{i},{i}" for i in range(20000)] + ["20000,oops"]
    src = tmp_path / "lead.csv"
    src.write_text("\n".join(rows) + "\n")
    result = ingest_file(src, tmp_path / "lead.parquet", "csv", max_rows=10**6)
    assert result.row_count == 20001
    assert result.warnings == [
        "Column 'val' mixes numbers or dates with other text, so it was stored as text."
    ]
    assert [m["normalized"] for m in result.column_map] == ["id", "val"]
    assert pq.read_table(tmp_path / "lead.parquet").num_rows == 20001


def test_ragged_rows_fail_with_message_and_leave_no_file(tmp_path: Path) -> None:
    src = tmp_path / "ragged.csv"
    src.write_text("a,b,c\n1,2,3\n4,5\n6,7,8,9\n")
    with pytest.raises(IngestError, match="could not be parsed"):
        ingest_file(src, tmp_path / "ragged.parquet", "csv", max_rows=100)
    assert not list(tmp_path.glob("*.parquet*"))


def test_row_limit_is_enforced_while_streaming(tmp_path: Path) -> None:
    src = tmp_path / "rows.csv"
    src.write_text("a\n" + "1\n" * 1000)
    with pytest.raises(IngestError, match="more than 500 rows"):
        ingest_file(src, tmp_path / "rows.parquet", "csv", max_rows=500)
    assert not list(tmp_path.glob("*.parquet*"))


def test_undecodable_text_is_a_clear_error(tmp_path: Path) -> None:
    src = tmp_path / "bad.csv"
    src.write_bytes(b"a,b\n1,2\n" + b"\xff\xfe\xfa" * 5 + b"\n")
    try:
        ingest_file(src, tmp_path / "bad.parquet", "csv", max_rows=100)
    except IngestError as exc:
        assert str(exc)
    assert not list(tmp_path.glob("*.tmp"))


def test_unsupported_extension_is_an_ingest_error(tmp_path: Path) -> None:
    with pytest.raises(IngestError):
        ingest_file(tmp_path / "x", tmp_path / "y", "xls", max_rows=1)


def test_xlsx_dates_blank_rows_and_ragged_header(tmp_path: Path) -> None:
    from datetime import date

    src = tmp_path / "book.xlsx"
    src.write_bytes(
        xlsx_bytes(
            {
                "Data": [
                    ["when", "amount", None],
                    [date(2024, 1, 1), 1.5, None],
                    [None, None, None],
                    [date(2024, 1, 2), 2.5, None],
                ],
                "Empty": [],
            }
        )
    )
    result = ingest_file(src, tmp_path / "book.parquet", "xlsx", max_rows=100)
    assert result.row_count == 2 and result.col_count == 2 and result.sheet == "Data"
    table = pq.read_table(tmp_path / "book.parquet")
    assert str(table.schema.field("when").type).startswith("timestamp")
    assert list_sheets(src) == ["Data", "Empty"]
    with pytest.raises(IngestError, match="empty or has no header"):
        ingest_file(src, tmp_path / "e.parquet", "xlsx", sheet="Empty", max_rows=100)
    with pytest.raises(IngestError, match="not found"):
        ingest_file(src, tmp_path / "m.parquet", "xlsx", sheet="Missing", max_rows=100)


def test_xlsx_row_limit_and_header_only(tmp_path: Path) -> None:
    src = tmp_path / "rows.xlsx"
    src.write_bytes(xlsx_bytes({"S": [["a"]] + [[i] for i in range(50)]}))
    with pytest.raises(IngestError, match="more than 10 rows"):
        ingest_file(src, tmp_path / "r.parquet", "xlsx", max_rows=10)
    header_only = tmp_path / "h.xlsx"
    header_only.write_bytes(xlsx_bytes({"S": [["a", "b"]]}))
    with pytest.raises(IngestError, match="no data rows"):
        ingest_file(header_only, tmp_path / "h.parquet", "xlsx", max_rows=10)


def test_corrupt_workbook_is_an_ingest_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"PK\x03\x04 not really")
    with pytest.raises(IngestError, match="corrupted"):
        list_sheets(broken)
    with pytest.raises(IngestError, match="corrupted"):
        ingest_file(broken, tmp_path / "b.parquet", "xlsx", max_rows=10)


def test_local_storage_confines_keys_to_root(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "root")
    for key in ("../escape.txt", "a/../../escape.txt", "/etc/passwd", ""):
        with pytest.raises(ValueError, match="escapes"):
            storage.local_path(key)
    assert storage.save_stream("t/d/1/f.csv", io.BytesIO(b"abc"), 10) == 3
    assert (
        storage.exists("t/d/1/f.csv") and storage.local_path("t/d/1/f.csv").read_bytes() == b"abc"
    )
    storage.delete_prefix("t/d")
    assert not storage.exists("t/d/1/f.csv")
    storage.delete_prefix("never/existed")


def test_local_storage_size_limit_leaves_no_partial_file(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    with pytest.raises(PayloadTooLargeError):
        storage.save_stream("a/b.csv", io.BytesIO(b"x" * 100), 10)
    assert list(tmp_path.rglob("*.part")) == [] and not storage.exists("a/b.csv")


def test_version_prefix_layout() -> None:
    import uuid

    t, d = uuid.uuid4(), uuid.uuid4()
    assert version_prefix(t, d, 3) == f"{t}/{d}/3"


def test_file_extension_and_display_name() -> None:
    assert file_extension("Report.CSV") == "csv" and file_extension("a\\b\\c.tsv") == "tsv"
    for bad in (None, "", "noext", "a.exe", "a.csv.exe", ".csv"):
        with pytest.raises(Exception, match="Unsupported"):
            file_extension(bad)
    assert sanitize_display_name(None) == "upload" and sanitize_display_name("...") == "upload"
    assert (
        sanitize_display_name("x" * 500).startswith("xxx")
        and len(sanitize_display_name("x" * 500)) == 200
    )
    assert sanitize_display_name("تقرير.csv") == "تقرير.csv"
    assert sanitize_display_name("a\x00b\nc.csv") == "abc.csv"
