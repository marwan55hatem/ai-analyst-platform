"""SafeQueryService: read-only, tenant-scoped SQL over one dataset version.

Defence in depth, because the SQL may come from users or an LLM:
1. the version must belong to the caller's tenant (repository filter);
2. a fresh in-memory DuckDB per query, with the Parquet loaded into table `data`;
3. external access disabled and the configuration locked before any user SQL runs;
4. sqlglot validation: one SELECT statement that only touches `data`, no table
   functions, no system functions; the *regenerated* SQL is what is executed, so a
   parser disagreement cannot smuggle a second statement through.
"""

import math
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal
from typing import Any

import duckdb
import sqlglot
from sqlalchemy.orm import Session
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError, UnprocessableError
from app.db.repository import DatasetVersionRepository
from app.models.dataset import VersionStatus
from app.services.storage import get_storage

DEFAULT_ROW_LIMIT = 1000
MAX_ROW_LIMIT = 100_000
MAX_SQL_CHARS = 20_000
TABLE_NAME = "data"

_DENIED_FUNCTIONS = frozenset(
    {
        "getenv",
        "current_setting",
        "query",
        "query_table",
        "load_extension",
        "install_extension",
        "glob",
        "sniff_csv",
        "which_secret",
        "current_database",
        "current_schema",
        "current_schemas",
        "current_catalog",
        "icu_collate",
        "getvariable",
        "checkpoint",
        "force_checkpoint",
        "pg_sleep",
        "sleep",
        "pragma_database_size",
        "pragma_version",
        "version",
        "setseed",
        "enable_logging",
        "disable_logging",
        "write_log",
        "create_secret",
        "drop_secret",
    }
)
_DENIED_PREFIXES = (
    "read_",
    "write_",
    "parquet_",
    "duckdb_",
    "pragma_",
    "iceberg_",
    "delta_",
    "sqlite_",
    "postgres_",
    "mysql_",
    "json_each_file",
    "st_read",
    "glob",
    "list_files",
    "copy_",
    "export_",
    "import_",
    "load_",
    "install_",
    "attach_",
    "query_",
    "http_",
    "s3_",
)
_DENIED_SUFFIXES = ("_scan", "_auto")
_SOURCE_TYPES = (exp.Table, exp.Subquery, exp.Values, exp.Unnest)


class QueryRejectedError(UnprocessableError):
    """The SQL is not allowed (422)."""

    code = "query_rejected"


class QueryFailedError(UnprocessableError):
    """The SQL was valid but could not run (unknown column, type error, ...) (422)."""

    code = "query_failed"


class QueryTimeoutError(AppError):
    """The query exceeded its time limit (408)."""

    status_code = 408
    code = "query_timeout"


@dataclass
class QueryResult:
    """Rows and metadata returned by a query."""

    columns: list[str]
    column_types: list[str]
    rows: list[list[Any]]
    truncated: bool
    elapsed_ms: float
    row_limit: int


def _reject(message: str) -> QueryRejectedError:
    return QueryRejectedError(message)


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return str(node.sql_name()).lower()


def _check_function(node: exp.Func) -> None:
    name = _function_name(node)
    if (
        name in _DENIED_FUNCTIONS
        or name.startswith(_DENIED_PREFIXES)
        or name.endswith(_DENIED_SUFFIXES)
    ):
        raise _reject(f"Function '{name}' is not allowed.")


def _check_table(table: exp.Table, cte_names: set[str]) -> None:
    if not isinstance(table.this, exp.Identifier):
        raise _reject("Only the table 'data' can be queried (no files or table functions).")
    if table.args.get("db") or table.args.get("catalog"):
        raise _reject("Schema- or catalog-qualified tables are not allowed.")
    name = table.name.lower()
    if name != TABLE_NAME and name not in cte_names:
        raise _reject(f"Unknown table '{table.name}'. Query the table named 'data'.")


def validate_sql(sql: str) -> str:
    """Return the canonical, validated SQL or raise QueryRejectedError."""
    if not sql or not sql.strip():
        raise _reject("The query is empty.")
    if len(sql) > MAX_SQL_CHARS:
        raise _reject(f"The query is longer than {MAX_SQL_CHARS} characters.")
    if "\x00" in sql:
        raise _reject("The query contains invalid characters.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except RecursionError as exc:
        raise _reject("The query is nested too deeply.") from exc
    except SqlglotError as exc:
        raise _reject(f"The query could not be parsed: {str(exc).splitlines()[0]}") from exc
    if len(statements) != 1:
        raise _reject("Only a single SELECT statement is allowed.")
    root = statements[0]
    if not isinstance(root, exp.Select | exp.SetOperation):
        raise _reject("Only SELECT queries (optionally with WITH) are allowed.")

    cte_names = {str(cte.alias).lower() for cte in root.find_all(exp.CTE)}
    for node in root.walk():
        if isinstance(node, exp.Table):
            _check_table(node, cte_names)
        elif isinstance(node, exp.From | exp.Join):
            source = node.this
            if not isinstance(source, _SOURCE_TYPES):
                raise _reject("Only the table 'data' and subqueries can be used in FROM.")
        elif isinstance(node, exp.Into | exp.Lock | exp.Placeholder | exp.Parameter):
            raise _reject("This SQL construct is not allowed.")
        elif isinstance(node, exp.Command | exp.Set | exp.Pragma | exp.Attach | exp.Copy):
            raise _reject("Only SELECT queries are allowed.")
        if isinstance(node, exp.Func):
            _check_function(node)
    try:
        return root.sql(dialect="duckdb")
    except RecursionError as exc:
        raise _reject("The query is nested too deeply.") from exc
    except SqlglotError as exc:  # pragma: no cover - defensive
        raise _reject("The query could not be processed.") from exc


def _clean(value: Any) -> Any:
    """Make a DuckDB value JSON-safe."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date | dtime):
        return value.isoformat()
    if isinstance(value, bytes | bytearray):
        return bytes(value).hex()
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    return str(value)


class SafeQueryService:
    """Run validated read-only SQL against one tenant-owned dataset version."""

    def __init__(self, session: Session) -> None:
        """Bind to a DB session used only to authorise access to the version."""
        self.session = session

    def run(
        self,
        tenant_id: uuid.UUID,
        dataset_version_id: uuid.UUID,
        sql: str,
        row_limit: int = DEFAULT_ROW_LIMIT,
        timeout_s: float | None = None,
    ) -> QueryResult:
        """Validate and execute `sql`; rows are capped at `row_limit`."""
        settings = get_settings()
        limit = max(1, min(int(row_limit), MAX_ROW_LIMIT))
        timeout = timeout_s if timeout_s is not None else settings.query_timeout_s
        safe_sql = validate_sql(sql)
        parquet = self._parquet_path(tenant_id, dataset_version_id)

        started = time.perf_counter()
        con = self._connect(parquet)
        timer = threading.Timer(timeout, con.interrupt)
        timer.start()
        try:
            cursor = con.execute(f"SELECT * FROM ({safe_sql}) AS _q LIMIT {limit + 1}")  # noqa: S608
            fetched = cursor.fetchall()
            description = cursor.description or []
        except duckdb.InterruptException as exc:
            raise QueryTimeoutError(f"The query took longer than {timeout:g} seconds.") from exc
        except duckdb.Error as exc:
            if timer.finished.is_set():
                raise QueryTimeoutError(f"The query took longer than {timeout:g} seconds.") from exc
            raise QueryFailedError(_short_error(exc)) from exc
        finally:
            timer.cancel()
            con.close()
        truncated = len(fetched) > limit
        rows = [[_clean(v) for v in row] for row in fetched[:limit]]
        return QueryResult(
            columns=[d[0] for d in description],
            column_types=[str(d[1]) for d in description],
            rows=rows,
            truncated=truncated,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            row_limit=limit,
        )

    def _parquet_path(self, tenant_id: uuid.UUID, version_id: uuid.UUID) -> str:
        version = DatasetVersionRepository(self.session, tenant_id).get_for_query(version_id)
        if version is None:
            raise NotFoundError("Dataset version not found")
        if version.status != VersionStatus.ready.value or not version.parquet_path:
            raise ConflictError("The dataset version is not ready.", code="dataset_not_ready")
        return str(get_storage().local_path(version.parquet_path))

    @staticmethod
    def _connect(parquet: str) -> duckdb.DuckDBPyConnection:
        """Open an isolated in-memory database holding only table `data`, then lock it."""
        settings = get_settings()
        con = duckdb.connect(
            ":memory:",
            config={
                "memory_limit": f"{settings.query_memory_mb}MB",
                "threads": settings.query_threads,
                "autoload_known_extensions": False,
                "autoinstall_known_extensions": False,
                "max_temp_directory_size": "0KB",
            },
        )
        try:
            con.execute(f"CREATE TABLE {TABLE_NAME} AS SELECT * FROM read_parquet(?)", [parquet])
            con.execute("SET enable_external_access=false")
            con.execute("SET lock_configuration=true")
        except BaseException:
            con.close()
            raise
        return con


_ERROR_PREFIX = re.compile(r"^\w+ Error:\s*")


def _short_error(exc: Exception) -> str:
    """First line of a DuckDB error, without class prefix or LINE context."""
    first = str(exc).splitlines()[0] if str(exc) else "The query failed."
    return _ERROR_PREFIX.sub("", first)[:300]
