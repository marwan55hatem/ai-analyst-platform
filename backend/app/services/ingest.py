"""Convert an uploaded CSV/TSV/XLSX into Parquet using bounded memory."""

import codecs
import csv
import io
import re
import unicodedata
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
from charset_normalizer import from_bytes
from openpyxl import load_workbook
from openpyxl.workbook.workbook import Workbook

ALL_STRINGS_WARNING = (
    "Column types could not be inferred consistently, so every column was stored as text."
)
SAMPLE_BYTES = 1024 * 1024
# Types a text column may be promoted to, most specific first (a column gets the first
# one that every value parses as).
_CSV_CANDIDATES: tuple[pa.DataType, ...] = (
    pa.int64(),
    pa.float64(),
    pa.bool_(),
    pa.date32(),
    pa.timestamp("us"),
)
# RE2 pattern: a value that looks like a number or an ISO date (no backslashes needed).
_TYPED_LOOK = (
    "^ *[-+]?([0-9]+[.]?[0-9]*|[.][0-9]+)([eE][-+]?[0-9]+)? *$|^[0-9]{4}-[0-9]{2}-[0-9]{2}"
)
CSV_BLOCK_BYTES = 1024 * 1024
XLSX_BATCH_ROWS = 10_000
ROW_GROUP_ROWS = 100_000
MAX_COLUMN_NAME = 63
_CANDIDATE_DELIMITERS = (",", ";", "\t", "|")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


_IDENTIFIER_TOKENS = frozenset(
    {"id", "code", "number", "no", "num", "phone", "zip", "sku", "رقم", "كود", "هاتف", "جوال"}
)
_LEADING_ZERO = re.compile(r"^0[0-9]+$")
# RE2 version of the same rule, for Arrow compute.
_LEADING_ZERO_RE2 = "^0[0-9]+$"
_SAMPLE_ROWS = 20_000


class IngestError(Exception):
    """A user-facing reason why a file could not be converted."""


def is_identifier_name(name: str) -> bool:
    """Return True when a (normalized) column name looks like an identifier.

    Matches whole `_`-separated tokens (`order_id`, `zip`, `رقم_الطلب`) rather than substrings,
    so `paid` or `discount` are not treated as identifiers.
    """
    return any(token in _IDENTIFIER_TOKENS for token in name.lower().split("_"))


def leading_zero_columns(rows: Iterable[Sequence[str]], ncols: int) -> set[int]:
    """Return indexes of columns where a text value has leading zeros (`00123`)."""
    found: set[int] = set()
    for row in rows:
        for i in range(min(ncols, len(row))):
            if i not in found and _LEADING_ZERO.match(row[i].strip()):
                found.add(i)
        if len(found) == ncols:
            break
    return found


@dataclass
class IngestResult:
    """Outcome of converting one file."""

    row_count: int
    col_count: int
    column_map: list[dict[str, str]]
    encoding: str | None = None
    delimiter: str | None = None
    sheet: str | None = None
    warnings: list[str] = field(default_factory=list)


def normalize_columns(names: list[str]) -> list[str]:
    """Return unique snake_case names (unicode letters kept) for the given headers."""
    used: set[str] = set()
    result: list[str] = []
    for index, raw in enumerate(names, start=1):
        text = unicodedata.normalize("NFKC", str(raw).strip())
        text = _CAMEL.sub("_", text).lower()
        text = _NON_WORD.sub("_", text).strip("_")[:MAX_COLUMN_NAME].strip("_")
        if not text:
            text = f"col_{index}"
        elif text[0].isdigit():
            text = f"col_{text}"[:MAX_COLUMN_NAME]
        candidate, suffix = text, 2
        while candidate in used:
            tail = f"_{suffix}"
            candidate = f"{text[: MAX_COLUMN_NAME - len(tail)]}{tail}"
            suffix += 1
        used.add(candidate)
        result.append(candidate)
    return result


def _column_map(originals: list[str], normalized: list[str]) -> list[dict[str, str]]:
    return [{"original": o, "normalized": n} for o, n in zip(originals, normalized, strict=True)]


def detect_encoding(sample: bytes) -> str:
    """Guess the text encoding of a sample (BOMs, strict UTF-8, then charset-normalizer)."""
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    try:
        codecs.getincrementaldecoder("utf-8")().decode(sample, final=False)
        return "utf-8"
    except UnicodeDecodeError:
        pass
    best = from_bytes(sample).best()
    if best is None:
        raise IngestError("The text encoding of this file could not be determined.")
    return str(best.encoding)


def detect_delimiter(text: str) -> str:
    """Pick the delimiter that splits the first lines into the most consistent columns."""
    lines = [ln for ln in text.splitlines() if ln.strip()][:25]
    best, best_score = ",", (0, 0)
    for candidate in _CANDIDATE_DELIMITERS:
        try:
            counts = [len(row) for row in csv.reader(lines, delimiter=candidate)]
        except csv.Error:
            continue
        if not counts:
            continue
        width = max(set(counts), key=counts.count)
        score = (counts.count(width) if width > 1 else 0, width)
        if score > best_score:
            best, best_score = candidate, score
    return best


def _tmp(path: Path) -> Path:
    return path.with_name(path.name + ".tmp")


def _write_batches(
    batches: Iterable[pa.RecordBatch], dst: Path, names: list[str], max_rows: int
) -> int:
    """Stream record batches into a Parquet file; return the row count."""
    rows = 0
    writer: pq.ParquetWriter | None = None
    tmp = _tmp(dst)
    try:
        for batch in batches:
            if batch.num_rows == 0:
                continue
            rows += batch.num_rows
            if rows > max_rows:
                raise IngestError(f"The file has more than {max_rows:,} rows (the allowed limit).")
            table = pa.Table.from_batches([batch]).rename_columns(names)
            if writer is None:
                writer = pq.ParquetWriter(tmp, table.schema, compression="zstd")
            writer.write_table(table, row_group_size=ROW_GROUP_ROWS)
    except BaseException:
        if writer is not None:
            writer.close()
            writer = None
        tmp.unlink(missing_ok=True)
        raise
    finally:
        if writer is not None:
            writer.close()
    if rows == 0:
        tmp.unlink(missing_ok=True)
        raise IngestError("The file has a header but no data rows.")
    tmp.replace(dst)
    return rows


def _iter_batches(reader: pacsv.CSVStreamingReader) -> Iterator[pa.RecordBatch]:
    while True:
        try:
            yield reader.read_next_batch()
        except StopIteration:
            return


def _open_csv(
    src: Path,
    encoding: str,
    delimiter: str,
    strings_only: tuple[int, int] | None,
    text_columns: Sequence[str] = (),
) -> pacsv.CSVStreamingReader:
    """Open a streaming reader; `strings_only=(skip_rows, ncols)` forces all-text columns.

    `text_columns` names columns (by header) that must stay text in the inferring reader.
    """
    arrow_encoding = "utf-8" if encoding == "utf-8-sig" else encoding
    if strings_only is None:
        read_opts = pacsv.ReadOptions(encoding=arrow_encoding, block_size=CSV_BLOCK_BYTES)
        convert_opts = pacsv.ConvertOptions(
            strings_can_be_null=True, column_types={c: pa.string() for c in text_columns}
        )
    else:
        skip, ncols = strings_only
        cols = [f"c{i}" for i in range(ncols)]
        read_opts = pacsv.ReadOptions(
            encoding=arrow_encoding,
            block_size=CSV_BLOCK_BYTES,
            skip_rows=skip,
            column_names=cols,
        )
        convert_opts = pacsv.ConvertOptions(
            strings_can_be_null=True, column_types={c: pa.string() for c in cols}
        )
    return pacsv.open_csv(
        src,
        read_options=read_opts,
        parse_options=pacsv.ParseOptions(delimiter=delimiter),
        convert_options=convert_opts,
    )


def _plan_csv_types(
    src: Path, options: tuple[str, str, int], ncols: int, forced_text: Collection[int] = ()
) -> tuple[list[pa.DataType], list[bool]]:
    """Scan the file as text and pick, per column, the most specific type every value fits.

    Columns in `forced_text` (identifier-like names) and columns holding values with leading
    zeros always stay text. Returns the chosen types and, per column, whether any value looked
    like a number or date (used to report columns that mix such values with other text).
    """
    encoding, delimiter, skip = options
    viable = [[] if i in forced_text else list(_CSV_CANDIDATES) for i in range(ncols)]
    seen = [False] * ncols
    typed_look = [False] * ncols
    zeros = set(forced_text)
    for batch in _iter_batches(_open_csv(src, encoding, delimiter, (skip, ncols))):
        for i in range(ncols):
            column = batch.column(i)
            if column.null_count == len(column):
                continue
            seen[i] = True
            has_zeros = pc.match_substring_regex(column, _LEADING_ZERO_RE2)
            if i not in zeros and pc.any(has_zeros).as_py():
                zeros.add(i)
                viable[i] = []
                typed_look[i] = False
            if i in zeros:
                continue
            if not typed_look[i]:
                typed_look[i] = bool(pc.any(pc.match_substring_regex(column, _TYPED_LOOK)).as_py())
            for candidate in list(viable[i]):
                try:
                    pc.cast(column, candidate)
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                    viable[i].remove(candidate)
    types = [v[0] if (v and seen[i]) else pa.string() for i, v in enumerate(viable)]
    return types, typed_look


def _text_fallback_warnings(
    originals: list[str], types: list[pa.DataType], typed_look: list[bool]
) -> list[str]:
    """One warning per column that mixes numbers/dates with other text and became text."""
    return [
        f"Column '{name}' mixes numbers or dates with other text, so it was stored as text."
        for name, kind, looked in zip(originals, types, typed_look, strict=True)
        if looked and pa.types.is_string(kind)
    ]


def _cast_batches(
    reader: pacsv.CSVStreamingReader, types: list[pa.DataType]
) -> Iterator[pa.RecordBatch]:
    for batch in _iter_batches(reader):
        arrays = [pc.cast(batch.column(i), kind) for i, kind in enumerate(types)]
        yield pa.RecordBatch.from_arrays(arrays, names=batch.schema.names)


def _csv_per_column_fallback(
    src: Path,
    dst: Path,
    options: tuple[str, str, int],
    originals: list[str],
    names: list[str],
    max_rows: int,
    forced_text: Collection[int] = (),
) -> tuple[int, list[str]]:
    """Re-read a CSV whose types could not be inferred up front.

    Each column is promoted independently (int -> float, then bool, date, timestamp); only a
    column that cannot be parsed as any of those becomes text. If even that fails the whole
    file is stored as text as a last resort.
    """
    encoding, delimiter, skip = options
    ncols = len(originals)
    try:
        types, typed_look = _plan_csv_types(src, options, ncols, forced_text)
        reader = _open_csv(src, encoding, delimiter, (skip, ncols))
        rows = _write_batches(_cast_batches(reader, types), dst, names, max_rows)
        return rows, _text_fallback_warnings(originals, types, typed_look)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        _tmp(dst).unlink(missing_ok=True)
    try:
        reader = _open_csv(src, encoding, delimiter, (skip, ncols))
        return _write_batches(_iter_batches(reader), dst, names, max_rows), [ALL_STRINGS_WARNING]
    except pa.ArrowInvalid as exc:
        _tmp(dst).unlink(missing_ok=True)
        raise IngestError(
            f"The file could not be parsed (inconsistent number of columns?): {exc}"
        ) from exc


def _csv_identifier_columns(lines: list[str], delimiter: str) -> tuple[list[str], set[int]]:
    """Parse the header of the sampled lines and return it with the columns to keep as text.

    A column is kept as text when its name looks like an identifier or a sampled value has
    leading zeros.
    """
    rows = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    header = [h.lstrip("﻿") for h in next(rows, [])]
    names = normalize_columns(header)
    forced = {i for i, name in enumerate(names) if is_identifier_name(name)}

    def sample() -> Iterator[list[str]]:
        try:
            for count, row in enumerate(rows):
                if count >= _SAMPLE_ROWS:
                    return
                yield row
        except csv.Error:
            return

    forced |= leading_zero_columns(sample(), len(header))
    return header, forced


def _ingest_csv(src: Path, dst: Path, ext: str, max_rows: int) -> IngestResult:
    with src.open("rb") as handle:
        sample = handle.read(SAMPLE_BYTES)
    if not sample.strip():
        raise IngestError("The file is empty.")
    encoding = detect_encoding(sample)
    text = sample.decode("utf-16" if encoding == "utf-16" else encoding, errors="replace")
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    leading_blank = next((i for i, ln in enumerate(lines) if ln != ""), len(lines))
    first = lines[leading_blank] if leading_blank < len(lines) else ""
    if not first.strip(" ,;\t|\"'"):
        raise IngestError("The file has no header row (the first line is empty).")
    delimiter = "\t" if ext == "tsv" else detect_delimiter(text)

    header, forced = _csv_identifier_columns(lines[leading_blank:], delimiter)
    warnings: list[str] = []
    try:
        text_columns = [header[i] for i in forced if i < len(header)]
        reader = _open_csv(src, encoding, delimiter, None, text_columns)
        originals = [n.lstrip("\ufeff") for n in reader.schema.names]
        names = normalize_columns(originals)
        rows = _write_batches(_iter_batches(reader), dst, names, max_rows)
    except pa.ArrowInvalid:
        # Typically a later value does not fit the types inferred from the first block.
        _tmp(dst).unlink(missing_ok=True)
        originals = header
        names = normalize_columns(originals)
        skip = leading_blank + 1
        rows, warnings = _csv_per_column_fallback(
            src, dst, (encoding, delimiter, skip), originals, names, max_rows, forced
        )
    except UnicodeDecodeError as exc:
        _tmp(dst).unlink(missing_ok=True)
        raise IngestError(f"The file is not valid {encoding} text.") from exc
    except IngestError:
        _tmp(dst).unlink(missing_ok=True)
        raise
    return IngestResult(
        row_count=rows,
        col_count=len(originals),
        column_map=_column_map(originals, names),
        encoding=encoding,
        delimiter=delimiter,
        warnings=warnings,
    )


def _open_workbook(src: Path) -> Workbook:
    try:
        return load_workbook(src, read_only=True, data_only=True)
    except Exception as exc:
        raise IngestError("The Excel file could not be opened (it may be corrupted).") from exc


def list_sheets(src: Path) -> list[str]:
    """Return the sheet names of an .xlsx file."""
    workbook = _open_workbook(src)
    try:
        return list(workbook.sheetnames)
    finally:
        workbook.close()


def _cell_text(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _sheet_rows(
    workbook: Workbook, sheet: str | None
) -> tuple[str, list[str], Iterator[tuple[Any, ...]]]:
    """Return (sheet name, header names, iterator over the remaining rows)."""
    if sheet is None:
        sheet = workbook.sheetnames[0] if workbook.sheetnames else None
    if sheet is None or sheet not in workbook.sheetnames:
        raise IngestError(f"Sheet '{sheet}' was not found in the workbook.")
    worksheet = workbook[sheet]
    worksheet.reset_dimensions()
    rows = iter(worksheet.iter_rows(values_only=True))
    for row in rows:
        if any(cell is not None and str(cell).strip() for cell in row):
            names = [str(c).strip() if c is not None else "" for c in row]
            while names and not names[-1]:
                names.pop()
            return sheet, names, rows
    raise IngestError("The selected sheet is empty or has no header row.")


def _id_text(value: Any) -> str | None:
    """Render a cell of an identifier column as text (`123.0` becomes `123`)."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _cell_text(value)


def _classify(value: Any) -> str | None:
    """Return the kind of a cell value (None for empty cells and blank text)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float | Decimal):
        return "float"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if _LEADING_ZERO.match(text):
            return "zstr"
        if text[:1] in "0123456789+-.":
            try:
                float(text)
            except ValueError:
                return "str"
            return "numstr"
        return "str"
    return "str"


def _resolve_xlsx_type(kinds: set[str]) -> pa.DataType:
    """Pick the column type for the set of cell kinds seen in it."""
    numeric = kinds & {"int", "float"}
    if "zstr" in kinds:
        return pa.string()
    if kinds and kinds <= {"int"}:
        return pa.int64()
    if numeric and kinds <= {"int", "float", "numstr"}:
        return pa.float64()
    if kinds == {"bool"}:
        return pa.bool_()
    if kinds == {"date"}:
        return pa.date32()
    if kinds and kinds <= {"date", "datetime"}:
        return pa.timestamp("us")
    return pa.string()


def _convert_cell(value: Any, target: pa.DataType) -> Any:
    """Convert a cell to the planned column type (every value is known to fit)."""
    if pa.types.is_string(target):
        return _cell_text(value)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if pa.types.is_integer(target):
        return int(value)
    if pa.types.is_floating(target):
        return float(value.strip() if isinstance(value, str) else value)
    if pa.types.is_timestamp(target):
        return value if isinstance(value, datetime) else datetime.combine(value, time())
    return value


def _plan_xlsx_types(
    src: Path, sheet: str | None, ncols: int, forced_text: Collection[int] = ()
) -> tuple[list[pa.DataType], list[int]]:
    """Scan the sheet and choose each column's type independently.

    Also returns the indexes of columns that mix numbers/dates with other text
    (they end up as text); the caller maps these to warnings. Identifier columns in
    `forced_text` always stay text and are never reported as mixed.
    """
    kinds: list[set[str]] = [set() for _ in range(ncols)]
    workbook = _open_workbook(src)
    try:
        _, _, rows = _sheet_rows(workbook, sheet)
        for row in rows:
            for i in range(ncols):
                kind = _classify(row[i] if i < len(row) else None)
                if kind:
                    kinds[i].add(kind)
    finally:
        workbook.close()
    types = [
        pa.string() if i in forced_text else _resolve_xlsx_type(k) for i, k in enumerate(kinds)
    ]
    mixed = [
        i
        for i, (k, t) in enumerate(zip(kinds, types, strict=True))
        if i not in forced_text
        and "zstr" not in k
        and pa.types.is_string(t)
        and "str" in k
        and k - {"str"}
    ]
    return types, mixed


def _strict_array(values: list[Any], target: pa.DataType) -> pa.Array:
    """Build an Arrow array, refusing to truncate decimals into an integer column.

    pyarrow silently turns 2.5 into 2 when asked for int64, which would corrupt data.
    """
    if pa.types.is_integer(target) and any(isinstance(v, float | Decimal) for v in values):
        raise pa.ArrowInvalid("decimal value in an integer column")
    return pa.array(values, type=target)


def _xlsx_batches(
    rows: Iterator[tuple[Any, ...]],
    ncols: int,
    *,
    types: list[pa.DataType] | None = None,
    strings_only: bool = False,
    text_cols: Collection[int] = (),
) -> Iterator[pa.RecordBatch]:
    """Yield batches of XLSX_BATCH_ROWS rows.

    Without `types` the column types come from the first batch and a later mismatch raises an
    Arrow error (the caller then plans types per column); with `types` every cell is converted.
    Columns in `text_cols` (identifiers) are always written as text.
    """
    chunk: list[list[Any]] = []
    schema: pa.Schema | None = None
    labels = [f"c{i}" for i in range(ncols)]

    def build_column(i: int, col: list[Any]) -> pa.Array:
        if i in text_cols:
            return pa.array([_id_text(v) for v in col], type=pa.string())
        if strings_only:
            return pa.array([_cell_text(v) for v in col], type=pa.string())
        if types is not None:
            return pa.array([_convert_cell(v, types[i]) for v in col], type=types[i])
        if schema is None:
            return pa.array(col)
        return _strict_array(col, schema.field(i).type)

    def build() -> pa.RecordBatch:
        columns = [[r[i] for r in chunk] for i in range(ncols)]
        arrays = [build_column(i, col) for i, col in enumerate(columns)]
        return pa.RecordBatch.from_arrays(arrays, names=labels)

    for row in rows:
        values = list(row[:ncols]) + [None] * (ncols - len(row))
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in values):
            continue
        chunk.append(values)
        if len(chunk) >= XLSX_BATCH_ROWS:
            batch = build()
            schema = schema or batch.schema
            yield batch
            chunk = []
    if chunk:
        yield build()


_XLSX_CONVERSION_ERRORS = (pa.ArrowInvalid, pa.ArrowTypeError, ValueError, OverflowError)


def _convert_xlsx(
    src: Path,
    dst: Path,
    sheet: str | None,
    max_rows: int,
    *,
    types: list[pa.DataType] | None = None,
    strings_only: bool = False,
    text_cols: Collection[int] = (),
) -> int:
    """Run one conversion attempt and return the row count."""
    workbook = _open_workbook(src)
    try:
        _, originals, rows = _sheet_rows(workbook, sheet)
        names = normalize_columns(originals)
        batches = _xlsx_batches(
            rows, len(originals), types=types, strings_only=strings_only, text_cols=text_cols
        )
        return _write_batches(batches, dst, names, max_rows)
    finally:
        workbook.close()


def _ingest_xlsx(src: Path, dst: Path, sheet: str | None, max_rows: int) -> IngestResult:
    workbook = _open_workbook(src)
    try:
        sheet_name, originals, _ = _sheet_rows(workbook, sheet)
    finally:
        workbook.close()
    names = normalize_columns(originals)
    forced = {i for i, name in enumerate(names) if is_identifier_name(name)}
    warnings: list[str] = []
    try:
        count = _convert_xlsx(src, dst, sheet_name, max_rows, text_cols=forced)
    except _XLSX_CONVERSION_ERRORS:
        try:
            types, mixed = _plan_xlsx_types(src, sheet_name, len(originals), forced)
            count = _convert_xlsx(src, dst, sheet_name, max_rows, types=types, text_cols=forced)
            warnings = [
                f"Column '{originals[i]}' mixes numbers or dates with other text, "
                "so it was stored as text."
                for i in mixed
            ]
        except _XLSX_CONVERSION_ERRORS:
            try:  # last resort: everything as text
                count = _convert_xlsx(src, dst, sheet_name, max_rows, strings_only=True)
            except _XLSX_CONVERSION_ERRORS as exc:
                raise IngestError(f"The sheet could not be converted: {exc}") from exc
            warnings = [ALL_STRINGS_WARNING]
    return IngestResult(
        row_count=count,
        col_count=len(originals),
        column_map=_column_map(originals, names),
        sheet=sheet_name,
        warnings=warnings,
    )


def ingest_file(
    src: Path, dst: Path, ext: str, *, sheet: str | None = None, max_rows: int
) -> IngestResult:
    """Convert `src` to Parquet at `dst` and return what was found.

    Memory use is bounded by the batch size, not the file size. Types are inferred from the
    first rows; if a later value does not fit, the file is re-read and each column is promoted
    on its own (int -> float, ...), and only a column that cannot be parsed becomes text.
    """
    if ext == "xlsx":
        return _ingest_xlsx(src, dst, sheet, max_rows)
    if ext in {"csv", "tsv"}:
        return _ingest_csv(src, dst, ext, max_rows)
    raise IngestError(f"Unsupported file type: {ext}")
