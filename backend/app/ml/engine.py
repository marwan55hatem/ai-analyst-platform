"""One hardened in-memory DuckDB connection per analysis run.

The role columns of the Parquet file are loaded once into a typed table called `data`
(dates as DATE, amounts as exact DECIMAL), then external access and configuration changes are
switched off, the same lockdown `SafeQueryService` applies. Modules aggregate in SQL and only
hand small result sets to pandas or statsmodels. Every SQL string a module stores as evidence is
the exact text that ran, so it also passes `validate_sql` and can be reproduced by the agent.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from app.core.config import get_settings
from app.ml.roles import RoleMap
from app.services.profiling.parsing import date_sql, number_sql, quote_ident, quote_literal

TABLE = "data"
# 1-based position of each row in the version's stored data (row-level findings point at it)
ROW_NUMBER = "__row_number"
_NUMERIC = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT", "DOUBLE", "DECIMAL")
_DECIMAL_TYPES = {
    "revenue": "DECIMAL(24,4)",
    "cost": "DECIMAL(24,4)",
    "quantity": "DECIMAL(12,4)",
    "price": "DECIMAL(18,4)",
    "discount": "DECIMAL(18,4)",
}
_TEXT_ROLES = ("customer_id", "order_id", "product", "category", "region", "channel")


class AnalysisConnection:
    """Thin wrapper around the locked-down connection."""

    def __init__(self, con: Any) -> None:
        """Wrap an open DuckDB connection."""
        self._con = con

    def rows(self, sql: str) -> list[dict[str, Any]]:
        """Run a query and return its rows as dicts (Decimal values stay exact)."""
        cursor = self._con.execute(sql)
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def batches(self, sql: str, size: int) -> Iterator[list[dict[str, Any]]]:
        """Run a query and yield its rows in batches, so large results never sit in memory."""
        cursor = self._con.cursor()
        try:
            cursor.execute(sql)
            names = [d[0] for d in cursor.description]
            while chunk := cursor.fetchmany(size):
                yield [dict(zip(names, row, strict=True)) for row in chunk]
        finally:
            cursor.close()

    def one(self, sql: str) -> dict[str, Any]:
        """Run a query that returns one row."""
        found = self.rows(sql)
        return found[0] if found else {}

    def scalar(self, sql: str) -> Any:
        """Run a query that returns one value."""
        row = self._con.execute(sql).fetchone()
        return None if row is None else row[0]

    def close(self) -> None:
        """Close the connection."""
        self._con.close()


def num(value: Any) -> float:
    """Convert a SQL number (Decimal, int, float or None) to float; None becomes 0.0."""
    if value is None:
        return 0.0
    return float(value) if isinstance(value, Decimal) else float(value)


def lit(value: date) -> str:
    """Return a DuckDB DATE literal for a date."""
    return f"DATE '{value.isoformat()}'"


def _select_item(name: str, role: str, dtype: str, roles: RoleMap) -> str:
    ident = quote_ident(name)
    upper = dtype.upper()
    if role == "date":
        if upper.startswith(("DATE", "TIMESTAMP")):
            expr = f"CAST({ident} AS DATE)"
        elif upper.startswith("VARCHAR"):
            expr = f"TRY_CAST({date_sql(ident, dayfirst=roles.dayfirst.get(name, True))} AS DATE)"
        else:
            expr = "NULL::DATE"
    elif role in _DECIMAL_TYPES:
        target = _DECIMAL_TYPES[role]
        if upper.startswith(_NUMERIC):
            expr = f"TRY_CAST({ident} AS {target})"
        elif upper.startswith("VARCHAR"):
            expr = f"TRY_CAST({number_sql(ident)} AS {target})"
        else:
            expr = f"NULL::{target}"
    else:
        expr = f"NULLIF(trim(CAST({ident} AS VARCHAR)), '')"
    return f"{expr} AS {ident}"


def _column_roles(roles: RoleMap) -> dict[str, str]:
    """Map each loaded column to the role that decides how it is converted."""
    mapping: dict[str, str] = {}
    for role, name in roles.columns.items():
        if role in {"date", *_DECIMAL_TYPES, *_TEXT_ROLES}:
            mapping[name] = role
    return mapping


@contextmanager
def open_analysis(parquet: Path, roles: RoleMap) -> Iterator[AnalysisConnection]:
    """Yield a locked-down connection whose table `data` holds the role columns of the file."""
    settings = get_settings()
    con = duckdb.connect(
        ":memory:",
        config={
            "memory_limit": f"{settings.analysis_memory_mb}MB",
            "threads": settings.query_threads,
            "autoload_known_extensions": False,
            "autoinstall_known_extensions": False,
            "max_temp_directory_size": "0KB",
        },
    )
    try:
        plain = f"read_parquet({quote_literal(str(parquet))})"
        types = {
            str(r[0]): str(r[1]) for r in con.execute(f"DESCRIBE SELECT * FROM {plain}").fetchall()
        }
        wanted = _column_roles(roles)
        missing = [n for n in wanted if n not in types]
        if missing:
            raise ValueError(f"columns missing from the file: {', '.join(missing)}")
        items = [_select_item(n, r, types[n], roles) for n, r in wanted.items()]
        if "file_row_number" in types:  # a user column of that name hides DuckDB's virtual one
            source = plain
            items.append(f"row_number() OVER () AS {ROW_NUMBER}")
        else:
            source = f"read_parquet({quote_literal(str(parquet))}, file_row_number = true)"
            items.append(f"file_row_number + 1 AS {ROW_NUMBER}")
        con.execute(f"CREATE TABLE {TABLE} AS SELECT {', '.join(items)} FROM {source}")
        con.execute("SET enable_external_access=false")
        con.execute("SET lock_configuration=true")
        yield AnalysisConnection(con)
    finally:
        con.close()
