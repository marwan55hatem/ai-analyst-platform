"""Helpers for dataset tests: sample files, uploads and in-memory file builders."""

import io
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response
from openpyxl import Workbook

from tests.helpers import API, Account

SAMPLES = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "samples"


def sample(name: str) -> bytes:
    """Return the bytes of a committed sample file."""
    return (SAMPLES / name).read_bytes()


def upload(
    client: TestClient,
    account: Account,
    content: bytes,
    filename: str = "data.csv",
    name: str = "Sales",
    path: str = "/datasets",
    **fields: Any,
) -> Response:
    """POST a multipart upload to /datasets (or another upload path)."""
    data = {**({"name": name} if path == "/datasets" else {}), **fields}
    return client.post(
        f"{API}{path}", headers=account.headers, files={"file": (filename, content)}, data=data
    )


def csv_bytes(rows: list[list[object]], header: list[str], delimiter: str = ",") -> bytes:
    """Build a small CSV file."""
    lines = [delimiter.join(header)] + [delimiter.join(str(c) for c in row) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def xlsx_bytes(sheets: dict[str, list[list[Any]]]) -> bytes:
    """Build an .xlsx workbook with the given sheets (first row is the header)."""
    workbook = Workbook()
    first = True
    for title, rows in sheets.items():
        sheet = workbook.active if first else workbook.create_sheet()
        assert sheet is not None
        sheet.title = title
        first = False
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def upload_ready(
    client: TestClient, account: Account, content: bytes, filename: str
) -> dict[str, Any]:
    """Upload and return the dataset JSON, asserting it processed successfully."""
    response = upload(client, account, content, filename)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    assert body["status"] == "ready", body
    return body
