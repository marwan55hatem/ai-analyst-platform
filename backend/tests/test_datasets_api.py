"""Dataset API: upload, validation, versions, delete, preview, roles."""

import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.services import datasets as dataset_service
from app.services import ingest
from tests.dataset_helpers import csv_bytes, sample, upload, upload_ready, xlsx_bytes
from tests.helpers import API, add_member, register


def _preview(client: TestClient, account: Any, dataset_id: str, rows: int = 50) -> dict[str, Any]:
    response = client.get(
        f"{API}/datasets/{dataset_id}/preview", headers=account.headers, params={"rows": rows}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _storage_files() -> list[Path]:
    root = Path(get_settings().storage_dir)
    return [p for p in root.rglob("*") if p.is_file()]


def test_clean_csv_is_ingested_and_previewed(client: TestClient) -> None:
    owner = register(client)
    body = upload_ready(client, owner, sample("retail_clean.csv"), "retail_clean.csv")
    version = body["versions"][0]
    assert (version["row_count"], version["col_count"], version["version"]) == (400, 7, 1)
    assert version["delimiter"] == "," and version["encoding"] == "utf-8"
    assert body["current_version_id"] == version["id"]
    assert "original_path" not in version and "parquet_path" not in version

    preview = _preview(client, owner, body["id"], rows=5)
    assert [c["name"] for c in preview["columns"]][:3] == ["order_id", "order_date", "region"]
    assert preview["row_count"] == 5 and preview["total_rows"] == 400


def test_messy_csv_normalizes_columns_and_detects_semicolon(client: TestClient) -> None:
    owner = register(client)
    body = upload_ready(client, owner, sample("retail_messy.csv"), "retail_messy.csv")
    version = body["versions"][0]
    assert version["delimiter"] == ";" and version["row_count"] == 300
    names = [m["normalized"] for m in version["column_map"]]
    assert names == [
        "order_id",
        "order_date",
        "region",
        "product_name",
        "qty",
        "unit_price",
        "qty_2",
    ]
    assert version["column_map"][5]["original"] == "Unit Price ($)"


def test_tab_delimited_file(client: TestClient) -> None:
    owner = register(client)
    content = csv_bytes([[1, "a"], [2, "b"]], ["n", "label"], delimiter="\t")
    for filename in ("data.tsv", "data.csv"):
        body = upload_ready(client, owner, content, filename)
        assert body["versions"][0]["delimiter"] == "\t"
        assert _preview(client, owner, body["id"])["rows"] == [[1, "a"], [2, "b"]]


def test_arabic_windows_1256_file(client: TestClient) -> None:
    owner = register(client)
    body = upload_ready(client, owner, sample("retail_arabic_cp1256.csv"), "arabic.csv")
    version = body["versions"][0]
    assert version["encoding"] == "cp1256"
    assert [m["original"] for m in version["column_map"]][:2] == ["رقم_الطلب", "المدينة"]
    cities = {row[1] for row in _preview(client, owner, body["id"], 500)["rows"]}
    assert cities <= {"القاهرة", "الإسكندرية", "الجيزة", "أسوان", "الأقصر"} and len(cities) > 1


def test_xlsx_sheets_default_and_selected_sheet(client: TestClient) -> None:
    owner = register(client)
    content = sample("retail_two_sheets.xlsx")
    body = upload_ready(client, owner, content, "book.xlsx")
    assert body["versions"][0]["sheet"] == "Orders" and body["versions"][0]["row_count"] == 250
    sheets = client.get(f"{API}/datasets/{body['id']}/sheets", headers=owner.headers)
    assert sheets.json() == {"sheets": ["Orders", "Products"]}

    other = upload(client, owner, content, "book.xlsx", name="Prices", sheet="Products")
    assert other.json()["status"] == "ready"
    assert other.json()["versions"][0]["row_count"] == 5

    bad = upload(client, owner, content, "book.xlsx", sheet="Nope")
    assert bad.status_code == 422 and bad.json()["code"] == "unknown_sheet"


def test_sheets_of_csv_is_rejected(client: TestClient) -> None:
    owner = register(client)
    body = upload_ready(client, owner, sample("retail_clean.csv"), "a.csv")
    response = client.get(f"{API}/datasets/{body['id']}/sheets", headers=owner.headers)
    assert response.status_code == 422 and response.json()["code"] == "not_excel"


def test_empty_and_blank_files_are_rejected(client: TestClient) -> None:
    owner = register(client)
    for content in (b"", b"   \n\r\n  ", b"\xef\xbb\xbf"):
        response = upload(client, owner, content, "empty.csv")
        assert response.status_code == 422, content
        assert response.json()["code"] == "empty_file"
    assert client.get(f"{API}/datasets", headers=owner.headers).json() == []
    assert _storage_files() == [] or all(owner.tenant_id not in str(p) for p in _storage_files())


def test_no_header_row_is_rejected_when_processing(client: TestClient) -> None:
    owner = register(client)
    response = upload(client, owner, b",,,\n1,2,3\n", "blank_header.csv")
    body = response.json()
    assert response.status_code == 201 and body["status"] == "failed"
    assert "no header" in body["versions"][0]["error"].lower()


def test_header_only_file_fails_with_message(client: TestClient) -> None:
    owner = register(client)
    body = upload(client, owner, b"a,b,c\n", "header_only.csv").json()
    assert body["status"] == "failed"
    assert "no data rows" in body["versions"][0]["error"]
    blocked = client.get(f"{API}/datasets/{body['id']}/preview", headers=owner.headers)
    assert blocked.status_code == 409 and blocked.json()["code"] == "dataset_failed"


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("data.exe", csv_bytes([[1]], ["a"])),
        ("data.txt", csv_bytes([[1]], ["a"])),
        ("data", csv_bytes([[1]], ["a"])),
        ("data.xls", b"\xd0\xcf\x11\xe0" + b"0" * 50),
        ("data.csv", xlsx_bytes({"S": [["a"], [1]]})),
        ("data.xlsx", csv_bytes([[1]], ["a"])),
        ("data.csv", b"\x89PNG\r\n\x1a\n" + b"0" * 50),
        ("data.csv", b"%PDF-1.7\n" + b"0" * 50),
        ("data.csv", b"MZ" + b"\x00" * 50),
        ("data.csv", b"id,name\n1,\x00\x00\x00\n"),
    ],
)
def test_wrong_type_or_content_is_rejected(
    client: TestClient, filename: str, content: bytes
) -> None:
    owner = register(client)
    response = upload(client, owner, content, filename)
    assert response.status_code == 415, response.text
    assert response.json()["code"] in {"unsupported_media_type", "content_mismatch"}
    assert client.get(f"{API}/datasets", headers=owner.headers).json() == []


def test_oversized_file_is_rejected_and_leaves_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(client)
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    big = b"a,b\n" + b"1,2\n" * (400 * 1024)
    response = upload(client, owner, big, "big.csv")
    assert response.status_code == 413 and response.json()["code"] == "payload_too_large"
    assert "1 MB" in response.json()["message"]
    assert client.get(f"{API}/datasets", headers=owner.headers).json() == []
    assert not [p for p in _storage_files() if owner.tenant_id in str(p)]


@pytest.mark.parametrize(
    ("filename", "display"),
    [
        ("../../etc/passwd.csv", "passwd.csv"),
        ("..\\..\\windows\\evil.csv", "evil.csv"),
        ("/abs/path/report.csv", "report.csv"),
        ("na<me>:x|y?.csv", "namexy.csv"),
    ],
)
def test_client_filename_is_never_used_as_a_path(
    client: TestClient, filename: str, display: str
) -> None:
    owner = register(client)
    body = upload_ready(client, owner, csv_bytes([[1]], ["a"]), filename)
    assert body["versions"][0]["original_filename"] == display
    root = Path(get_settings().storage_dir).resolve()
    stored = [p for p in _storage_files() if owner.tenant_id in str(p)]
    assert stored and all(root in p.resolve().parents for p in stored)
    names = {p.name for p in stored}
    assert names == {"original.csv", "data.parquet"}
    parts = {p.resolve().relative_to(root).parts[0] for p in stored}
    assert parts == {owner.tenant_id}


def test_corrupted_excel_is_rejected(client: TestClient) -> None:
    owner = register(client)
    real = xlsx_bytes({"S": [["a", "b"], [1, 2]]})
    for content in (b"PK\x03\x04" + os.urandom(200), real[: len(real) // 2]):
        response = upload(client, owner, content, "broken.xlsx")
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "corrupt_file"
    zip_only = upload(client, owner, _plain_zip(), "notexcel.xlsx")
    assert zip_only.status_code == 415
    assert client.get(f"{API}/datasets", headers=owner.headers).json() == []


def _plain_zip() -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "hi")
    return buffer.getvalue()


def test_new_version_keeps_history_and_switches_current(client: TestClient) -> None:
    owner = register(client)
    first = upload_ready(client, owner, csv_bytes([[1, "a"]], ["id", "x"]), "v1.csv")
    second = upload(
        client,
        owner,
        csv_bytes([[1, "a"], [2, "b"]], ["id", "x"]),
        "v2.csv",
        path=f"/datasets/{first['id']}/versions",
    )
    assert second.status_code == 201, second.text
    body = second.json()
    assert [v["version"] for v in body["versions"]] == [2, 1]
    assert body["current_version_id"] == body["versions"][0]["id"]
    assert body["latest_version"]["row_count"] == 2
    assert _preview(client, owner, first["id"])["total_rows"] == 2

    broken = upload(
        client, owner, b"x,y\n", "v3.csv", path=f"/datasets/{first['id']}/versions"
    ).json()
    assert broken["versions"][0]["status"] == "failed"
    assert broken["status"] == "ready"  # the previous version stays usable
    assert broken["current_version_id"] == body["current_version_id"]


def test_version_upload_to_unknown_dataset_is_404(client: TestClient) -> None:
    owner = register(client)
    response = upload(client, owner, b"a\n1\n", "x.csv", path=f"/datasets/{uuid.uuid4()}/versions")
    assert response.status_code == 404


def test_roles_member_uploads_but_only_owner_or_admin_delete(client: TestClient) -> None:
    owner = register(client)
    member = add_member(client, owner, "member")
    admin = add_member(client, owner, "admin")
    made = upload_ready(client, member, csv_bytes([[1]], ["a"]), "m.csv")
    assert client.get(f"{API}/datasets", headers=member.headers).status_code == 200

    denied = client.delete(f"{API}/datasets/{made['id']}", headers=member.headers)
    assert denied.status_code == 403
    assert client.delete(f"{API}/datasets/{made['id']}", headers=admin.headers).status_code == 204

    again = upload_ready(client, member, csv_bytes([[1]], ["a"]), "m.csv")
    assert client.delete(f"{API}/datasets/{again['id']}", headers=owner.headers).status_code == 204


def test_soft_delete_hides_everything(client: TestClient, db: Any) -> None:
    owner = register(client)
    body = upload_ready(client, owner, csv_bytes([[1]], ["a"]), "d.csv")
    dataset_id = body["id"]
    assert client.delete(f"{API}/datasets/{dataset_id}", headers=owner.headers).status_code == 204

    assert client.get(f"{API}/datasets", headers=owner.headers).json() == []
    for method, suffix in (("GET", ""), ("GET", "/preview"), ("GET", "/sheets"), ("DELETE", "")):
        response = client.request(
            method, f"{API}/datasets/{dataset_id}{suffix}", headers=owner.headers
        )
        assert response.status_code == 404, (method, suffix)
    version_upload = upload(
        client, owner, b"a\n1\n", "x.csv", path=f"/datasets/{dataset_id}/versions"
    )
    assert version_upload.status_code == 404

    from app.models.dataset import Dataset

    row = db.get(Dataset, uuid.UUID(dataset_id))
    assert row is not None and row.deleted_at is not None  # soft: the row is kept


def test_preview_while_processing_is_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(client)
    monkeypatch.setattr(dataset_service, "enqueue_ingest", lambda *a, **k: None)
    body = upload(client, owner, csv_bytes([[1]], ["a"]), "p.csv").json()
    assert body["status"] == "uploaded"
    response = client.get(f"{API}/datasets/{body['id']}/preview", headers=owner.headers)
    assert response.status_code == 409 and response.json()["code"] == "dataset_not_ready"


def test_row_limit_fails_ingestion_with_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(client)
    monkeypatch.setattr(get_settings(), "max_rows", 100)
    body = upload(client, owner, sample("retail_clean.csv"), "c.csv").json()
    assert body["status"] == "failed"
    assert "more than 100 rows" in body["versions"][0]["error"]
    assert not [p for p in _storage_files() if owner.tenant_id in str(p) and p.suffix == ".parquet"]


def test_unexpected_ingest_error_is_reported_generically(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(client)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("secret internal detail /srv/path")

    monkeypatch.setattr(ingest, "ingest_file", boom)
    body = upload(client, owner, csv_bytes([[1]], ["a"]), "e.csv").json()
    assert body["status"] == "failed"
    assert "secret" not in body["versions"][0]["error"]


def test_queue_failure_marks_version_failed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workers import tasks

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(tasks.ingest_dataset, "delay", refuse)
    owner = register(client)
    body = upload(client, owner, csv_bytes([[1]], ["a"]), "q.csv").json()
    assert body["status"] == "failed"
    assert "could not be started" in body["versions"][0]["error"]


def test_list_and_get_include_latest_version(client: TestClient) -> None:
    owner = register(client)
    made = upload_ready(client, owner, csv_bytes([[1]], ["a"]), "l.csv")
    listing = client.get(f"{API}/datasets", headers=owner.headers).json()
    assert [d["id"] for d in listing] == [made["id"]]
    assert listing[0]["latest_version"]["status"] == "ready"
    paged = client.get(f"{API}/datasets", headers=owner.headers, params={"limit": 1, "offset": 1})
    assert paged.json() == []


def test_requires_authentication(client: TestClient) -> None:
    some_id = uuid.uuid4()
    calls = [
        ("GET", "/datasets"),
        ("POST", "/datasets"),
        ("GET", f"/datasets/{some_id}"),
        ("DELETE", f"/datasets/{some_id}"),
        ("POST", f"/datasets/{some_id}/versions"),
        ("GET", f"/datasets/{some_id}/sheets"),
        ("GET", f"/datasets/{some_id}/preview"),
    ]
    for method, path in calls:
        assert client.request(method, f"{API}{path}").status_code == 401, (method, path)


def test_client_cannot_inject_tenant_id(client: TestClient) -> None:
    a = register(client)
    b = register(client)
    response = upload(
        client, a, csv_bytes([[1]], ["a"]), "t.csv", tenant_id=b.tenant_id
    )  # extra form field is ignored, never used
    assert response.status_code == 201
    assert client.get(f"{API}/datasets", headers=b.headers).json() == []
    assert len(client.get(f"{API}/datasets", headers=a.headers).json()) == 1


def test_text_csv_starting_with_binary_looking_letters_is_accepted(client: TestClient) -> None:
    owner = register(client)
    for header in ("MZN", "MZ_code", "PAR1_id", "GIF8x"):
        body = upload_ready(client, owner, csv_bytes([[1, 2]], [header, "v"]), "letters.csv")
        assert body["versions"][0]["row_count"] == 1


def test_blank_name_is_rejected(client: TestClient) -> None:
    owner = register(client)
    response = upload(client, owner, csv_bytes([[1]], ["a"]), "n.csv", name="   ")
    assert response.status_code == 422


def test_older_version_finishing_late_does_not_replace_newer_current(client: TestClient) -> None:
    from app.db.session import get_sessionmaker
    from app.models.dataset import Dataset, DatasetVersion
    from app.services.datasets import run_ingest

    owner = register(client)
    first = upload_ready(client, owner, csv_bytes([[1]], ["a"]), "v1.csv")
    second = upload(
        client,
        owner,
        csv_bytes([[1], [2]], ["a"]),
        "v2.csv",
        path=f"/datasets/{first['id']}/versions",
    ).json()
    newest = second["current_version_id"]
    with get_sessionmaker()() as session:
        old = session.get(DatasetVersion, uuid.UUID(first["versions"][0]["id"]))
        assert old is not None
        old.status = "uploaded"
        session.commit()
        run_ingest(uuid.UUID(owner.tenant_id), old.id)
        session.expire_all()
        dataset = session.get(Dataset, uuid.UUID(first["id"]))
        assert dataset is not None and str(dataset.current_version_id) == newest
