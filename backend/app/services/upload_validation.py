"""Upload validation: extension, magic bytes, display-name sanitising."""

import re
import unicodedata
import zipfile
from pathlib import Path
from typing import BinaryIO

from app.core.errors import UnprocessableError, UnsupportedMediaError

ALLOWED_EXTENSIONS = {"csv", "tsv", "xlsx"}
HEAD_BYTES = 8192
_BINARY_SIGNATURES = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"%PDF",
    b"MZ\x90\x00",
    b"\x7fELF",
    b"\x89PNG",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"\xd0\xcf\x11\xe0",  # legacy .xls / OLE2
    b"\x1f\x8b",
    b"SQLite format 3",
    b"PAR1\x15",
)
_TEXT_BOMS = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")
_UNSAFE = re.compile(r"[\x00-\x1f\x7f<>:\"|?*]")


def sanitize_display_name(filename: str | None) -> str:
    """Return a safe display name (never used as a path): no directories or control chars."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFC", _UNSAFE.sub("", name)).strip().strip(".")
    return name[:200] or "upload"


def file_extension(filename: str | None) -> str:
    """Return the validated lowercase extension or raise 415."""
    suffix = Path((filename or "").replace("\\", "/").rsplit("/", 1)[-1]).suffix
    ext = suffix.lstrip(".").lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UnsupportedMediaError(
            "Unsupported file type. Upload a .csv, .tsv or .xlsx file.",
            details={"allowed": sorted(ALLOWED_EXTENSIONS)},
        )
    return ext


def check_head(source: BinaryIO, ext: str) -> None:
    """Check the first bytes of the upload against its extension, then rewind."""
    head = source.read(HEAD_BYTES)
    source.seek(0)
    if not head:
        raise UnprocessableError("The file is empty.", code="empty_file")
    if ext == "xlsx":
        if not head.startswith(b"PK\x03\x04"):
            raise UnsupportedMediaError(
                "The file is not a valid Excel (.xlsx) workbook.", code="content_mismatch"
            )
        return
    if head.startswith(_BINARY_SIGNATURES):
        raise UnsupportedMediaError(
            f"The file content is not text, so it cannot be a .{ext} file.",
            code="content_mismatch",
        )
    if b"\x00" in head and not head.startswith(_TEXT_BOMS):
        raise UnsupportedMediaError(
            f"The file contains binary data and is not a valid .{ext} file.",
            code="content_mismatch",
        )
    if not head.strip(b" \t\r\n\x00\xef\xbb\xbf\xff\xfe\xfe"):
        raise UnprocessableError("The file is empty (it has no header row).", code="empty_file")


def check_saved_xlsx(path: Path) -> None:
    """Verify a saved .xlsx is a real, readable workbook package."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "xl/workbook.xml" not in names or "[Content_Types].xml" not in names:
                raise UnsupportedMediaError(
                    "The file is a ZIP archive but not an Excel workbook.",
                    code="content_mismatch",
                )
            if archive.testzip() is not None:
                raise UnprocessableError("The Excel file is corrupted.", code="corrupt_file")
    except zipfile.BadZipFile as exc:
        raise UnprocessableError("The Excel file is corrupted.", code="corrupt_file") from exc
