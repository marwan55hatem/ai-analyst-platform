"""A memory-limited DuckDB connection over a version's Parquet file (trusted internal code).

Profiling and cleaning run inside the worker with our own SQL, so external file access is
needed to read the Parquet. Nothing here accepts SQL from users or an LLM; that goes through
`SafeQueryService`. The Parquet is exposed as a view, so DuckDB streams it instead of loading
the whole file into memory.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from app.core.config import get_settings
from app.services.profiling.parsing import quote_literal

SOURCE_VIEW = "data"


@contextmanager
def open_parquet(path: Path) -> Iterator[Any]:
    """Yield a DuckDB connection with the Parquet file available as the view `data`."""
    settings = get_settings()
    con = duckdb.connect(
        ":memory:",
        config={
            "memory_limit": f"{settings.profile_memory_mb}MB",
            "threads": settings.query_threads,
            "autoload_known_extensions": False,
            "autoinstall_known_extensions": False,
            "max_temp_directory_size": "0KB",
        },
    )
    try:
        con.execute(
            f"CREATE VIEW {SOURCE_VIEW} AS SELECT * FROM read_parquet({quote_literal(str(path))})"
        )
        yield con
    finally:
        con.close()


def schema_of(con: Any, relation: str = SOURCE_VIEW) -> list[tuple[str, str]]:
    """Return (column name, DuckDB type) pairs of a relation."""
    rows = con.execute(f"DESCRIBE {relation}").fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


def scalar(con: Any, sql: str) -> Any:
    """Run a query that returns one value."""
    row = con.execute(sql).fetchone()
    return None if row is None else row[0]
