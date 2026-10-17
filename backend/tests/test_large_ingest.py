"""A 100 MB-class CSV is converted without loading the file into memory."""

import tracemalloc
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services.ingest import ingest_file

TARGET_BYTES = 105 * 1024 * 1024
ROWS_PER_CHUNK = 50_000
MEMORY_BUDGET = 64 * 1024 * 1024  # peak allowed above the baseline; the file is >100 MB


def _write_big_csv(path: Path) -> int:
    """Stream a ~105 MB CSV to disk in chunks; return the number of data rows."""
    rows = 0
    with path.open("w", encoding="utf-8", newline="") as out:
        out.write("order_id,order_date,region,product,quantity,unit_price,customer_note\n")
        while out.tell() < TARGET_BYTES:
            lines = [
                f"{rows + i},2024-{1 + (i % 12):02d}-{1 + (i % 28):02d},"
                f"{('North', 'South', 'East', 'West')[i % 4]},product_{i % 500},{1 + i % 9},"
                f"{(i % 997) / 7:.2f},customer note number {rows + i} with padding text\n"
                for i in range(ROWS_PER_CHUNK)
            ]
            out.write("".join(lines))
            rows += ROWS_PER_CHUNK
    return rows


@pytest.mark.slow
def test_hundred_megabyte_csv_ingests_with_bounded_memory(tmp_path: Path) -> None:
    src = tmp_path / "big.csv"
    expected_rows = _write_big_csv(src)
    assert src.stat().st_size >= 100 * 1024 * 1024

    pool = pa.default_memory_pool()
    baseline = pool.bytes_allocated()
    tracemalloc.start()
    try:
        result = ingest_file(src, tmp_path / "big.parquet", "csv", max_rows=2_000_000)
        _, python_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    arrow_peak = pool.max_memory() - baseline

    assert result.row_count == expected_rows and result.col_count == 7
    assert not result.warnings
    assert python_peak < MEMORY_BUDGET, f"python peak {python_peak / 1e6:.0f} MB"
    assert arrow_peak < src.stat().st_size * 0.9, f"arrow peak {arrow_peak / 1e6:.0f} MB"
    assert pq.ParquetFile(tmp_path / "big.parquet").metadata.num_rows == expected_rows
