"""Per-column type fallback for CSV and XLSX uploads."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from app.services import ingest
from app.services.ingest import ingest_file
from app.services.query import SafeQueryService
from tests.dataset_helpers import csv_bytes, upload_ready, xlsx_bytes
from tests.helpers import API, Account, register

MIXED = "Column '{}' mixes numbers or dates with other text, so it was stored as text."


def _types(client: TestClient, owner: Account, dataset_id: str) -> dict[str, str]:
    response = client.get(
        f"{API}/datasets/{dataset_id}/preview", headers=owner.headers, params={"rows": 3}
    )
    assert response.status_code == 200, response.text
    return {c["name"]: c["type"] for c in response.json()["columns"]}


def _kinds(path: Path) -> dict[str, str]:
    return {f.name: str(f.type) for f in pq.read_schema(path)}


def test_bad_value_makes_only_that_csv_column_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(client)
    monkeypatch.setattr(ingest, "CSV_BLOCK_BYTES", 64 * 1024)
    rows = [[i, f"{i}.5", f"name{i}"] for i in range(20_000)]
    rows[5000][1] = '"$1,200"'
    body = upload_ready(client, owner, csv_bytes(rows, ["seq", "price", "name"]), "mixed.csv")
    version = body["versions"][0]
    assert version["row_count"] == 20_000
    assert version["warnings"] == [MIXED.format("price")]
    assert _types(client, owner, body["id"]) == {
        "seq": "BIGINT",
        "price": "VARCHAR",
        "name": "VARCHAR",
    }
    with get_sessionmaker()() as session:
        service = SafeQueryService(session)
        tenant, version_id = uuid.UUID(owner.tenant_id), uuid.UUID(version["id"])
        found = service.run(
            tenant, version_id, "SELECT seq, price FROM data WHERE price = '$1,200'"
        )
        total = service.run(tenant, version_id, "SELECT sum(seq) FROM data")
    assert found.rows == [[5000, "$1,200"]]  # the offending value survived as written
    assert total.rows == [[sum(range(20_000))]]  # id is still numeric


def test_integer_column_with_a_later_float_becomes_double_csv(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(client)
    monkeypatch.setattr(ingest, "CSV_BLOCK_BYTES", 16 * 1024)
    rows: list[list[Any]] = [[i, i * 2, f"n{i}"] for i in range(6000)]
    rows[3000][1] = "2.5"
    body = upload_ready(client, owner, csv_bytes(rows, ["seq", "qty", "name"]), "promote.csv")
    version = body["versions"][0]
    assert version["row_count"] == 6000 and not version["warnings"]
    assert _types(client, owner, body["id"]) == {
        "seq": "BIGINT",
        "qty": "DOUBLE",
        "name": "VARCHAR",
    }


def test_integer_column_with_a_later_float_becomes_double_xlsx(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(client)
    monkeypatch.setattr(ingest, "XLSX_BATCH_ROWS", 500)
    rows: list[list[Any]] = [["seq", "qty", "name"]] + [[i, i * 2, f"n{i}"] for i in range(6000)]
    rows[3001][1] = 2.5  # data row 3000
    body = upload_ready(client, owner, xlsx_bytes({"Data": rows}), "promote.xlsx")
    version = body["versions"][0]
    assert version["row_count"] == 6000 and not version["warnings"]
    assert _types(client, owner, body["id"]) == {
        "seq": "BIGINT",
        "qty": "DOUBLE",
        "name": "VARCHAR",
    }


def test_bad_value_makes_only_that_excel_column_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(client)
    monkeypatch.setattr(ingest, "XLSX_BATCH_ROWS", 500)
    rows: list[list[Any]] = [["seq", "amount", "qty"]] + [[i, float(i), i] for i in range(3000)]
    rows[2501][1] = "$1,200"
    body = upload_ready(client, owner, xlsx_bytes({"Data": rows}), "mixed.xlsx")
    version = body["versions"][0]
    assert version["row_count"] == 3000
    assert version["warnings"] == [MIXED.format("amount")]
    assert _types(client, owner, body["id"]) == {
        "seq": "BIGINT",
        "amount": "VARCHAR",
        "qty": "BIGINT",
    }


def test_excel_bad_value_inside_first_batch_is_still_per_column(client: TestClient) -> None:
    owner = register(client)
    rows: list[list[Any]] = [["seq", "amount"]] + [[i, float(i)] for i in range(100)]
    rows[10][1] = "n/a"
    body = upload_ready(client, owner, xlsx_bytes({"Data": rows}), "small.xlsx")
    assert _types(client, owner, body["id"]) == {"seq": "BIGINT", "amount": "VARCHAR"}
    assert body["versions"][0]["warnings"] == [MIXED.format("amount")]


def test_all_strings_is_the_last_resort(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise pa.ArrowInvalid("cannot plan")

    owner = register(client)
    monkeypatch.setattr(ingest, "CSV_BLOCK_BYTES", 16 * 1024)
    monkeypatch.setattr(ingest, "XLSX_BATCH_ROWS", 100)
    monkeypatch.setattr(ingest, "_plan_csv_types", broken)
    monkeypatch.setattr(ingest, "_plan_xlsx_types", broken)

    csv_rows: list[list[Any]] = [[i, i] for i in range(5000)]
    csv_rows[4000][1] = "oops"
    csv_body = upload_ready(client, owner, csv_bytes(csv_rows, ["a", "b"]), "last.csv")
    assert csv_body["versions"][0]["warnings"] == [ingest.ALL_STRINGS_WARNING]
    assert set(_types(client, owner, csv_body["id"]).values()) == {"VARCHAR"}

    xlsx_rows: list[list[Any]] = [["a", "b"]] + [[i, i] for i in range(500)]
    xlsx_rows[400][1] = "oops"
    xlsx_body = upload_ready(client, owner, xlsx_bytes({"S": xlsx_rows}), "last.xlsx")
    assert xlsx_body["versions"][0]["warnings"] == [ingest.ALL_STRINGS_WARNING]
    assert set(_types(client, owner, xlsx_body["id"]).values()) == {"VARCHAR"}


def test_csv_columns_are_promoted_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingest, "CSV_BLOCK_BYTES", 16 * 1024)
    rows = ["i,f,d,t,b,txt"]
    for n in range(6000):
        f = "2.5" if n == 4000 else str(n)
        stamp = "2024-01-02 03:04:05" if n == 4500 else "2024-01-02"
        flag = "true" if n % 2 else "false"
        rows.append(f"{n},{f},2024-01-02,{stamp},{flag},word{n}")
    src = tmp_path / "multi.csv"
    src.write_text("\n".join(rows) + "\n")
    result = ingest_file(src, tmp_path / "multi.parquet", "csv", max_rows=10**6)
    kinds = _kinds(tmp_path / "multi.parquet")
    assert kinds["i"] == "int64" and kinds["f"] == "double"
    assert kinds["d"].startswith("date32") and kinds["t"].startswith("timestamp")
    assert kinds["b"] == "bool" and kinds["txt"] == "string"
    assert result.warnings == []  # nothing mixed numbers with text


def test_all_null_column_becomes_text_and_others_keep_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingest, "CSV_BLOCK_BYTES", 16 * 1024)
    rows = ["a,empty,c"] + [f"{n},,{n}" for n in range(6000)]
    rows[5500] = "5500,,x"
    src = tmp_path / "nulls.csv"
    src.write_text("\n".join(rows) + "\n")
    ingest_file(src, tmp_path / "nulls.parquet", "csv", max_rows=10**6)
    assert _kinds(tmp_path / "nulls.parquet") == {"a": "int64", "empty": "string", "c": "string"}


def test_classify_and_resolve_xlsx_types() -> None:
    classify = ingest._classify
    assert classify(None) is None and classify("  ") is None
    assert classify(True) == "bool" and classify(3) == "int" and classify(Decimal("1.5")) == "float"
    assert classify(datetime(2024, 1, 1)) == "datetime" and classify(date(2024, 1, 1)) == "date"
    assert classify("12") == "numstr" and classify("-3.5") == "numstr"
    assert classify("$12") == "str" and classify("nan") == "str" and classify(object()) == "str"
    resolve = ingest._resolve_xlsx_type
    assert str(resolve({"int"})) == "int64" and str(resolve({"int", "float"})) == "double"
    assert str(resolve({"int", "numstr"})) == "double" and str(resolve({"numstr"})) == "string"
    assert str(resolve({"bool"})) == "bool" and str(resolve({"date"})).startswith("date32")
    assert str(resolve({"date", "datetime"})).startswith("timestamp")
    assert str(resolve({"int", "str"})) == "string" and str(resolve(set())) == "string"


def test_xlsx_dates_numeric_strings_and_blank_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingest, "XLSX_BATCH_ROWS", 50)
    data: list[list[object]] = [["when", "amount", "flag", "day"]]
    for n in range(200):
        when = datetime(2024, 1, 1, 12) if n == 150 else date(2024, 1, 1)
        amount = "12.5" if n == 120 else n
        data.append([when, amount, n % 2 == 0, date(2024, 1, 2) if n != 7 else None])
    src = tmp_path / "typed.xlsx"
    src.write_bytes(xlsx_bytes({"S": data}))
    result = ingest_file(src, tmp_path / "typed.parquet", "xlsx", max_rows=10**6)
    kinds = _kinds(tmp_path / "typed.parquet")
    assert kinds["when"].startswith("timestamp") and kinds["amount"] == "double"
    assert kinds["flag"] == "bool" and kinds["day"].startswith("timestamp")
    assert result.warnings == []
