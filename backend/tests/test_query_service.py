"""SafeQueryService: abuse suite (must all be rejected), valid suite, isolation, limits."""

import math
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.db.session import get_sessionmaker
from app.models.dataset import Dataset, DatasetVersion, VersionStatus
from app.services.query import (
    QueryFailedError,
    QueryRejectedError,
    QueryTimeoutError,
    SafeQueryService,
    _clean,
    _short_error,
    validate_sql,
)
from tests.dataset_helpers import sample, upload_ready
from tests.helpers import API, Account, register

ABUSE = [
    # DDL / DML
    "DROP TABLE data",
    "DELETE FROM data",
    "INSERT INTO data VALUES (1)",
    "UPDATE data SET region = 'x'",
    "CREATE TABLE t AS SELECT * FROM data",
    "CREATE VIEW v AS SELECT 1",
    "ALTER TABLE data ADD COLUMN x INT",
    "TRUNCATE data",
    "SELECT * INTO new_t FROM data",
    # files, attach, pragma, extensions, settings
    "COPY data TO 'out.csv'",
    "COPY (SELECT * FROM data) TO '/tmp/o.parquet'",
    "EXPORT DATABASE 'dir'",
    "ATTACH 'other.db' AS o",
    "DETACH o",
    "PRAGMA database_list",
    "CALL pragma_version()",
    "INSTALL httpfs",
    "LOAD httpfs",
    "SELECT load_extension('httpfs')",
    "SET enable_external_access = true",
    "SET memory_limit = '99GB'",
    "USE other",
    "VACUUM",
    "CHECKPOINT",
    "EXPLAIN SELECT * FROM data",
    "DESCRIBE data",
    "SHOW TABLES",
    # reading files through functions or string table names
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_csv_auto('x.csv')",
    "SELECT * FROM read_parquet('x.parquet')",
    "SELECT * FROM parquet_scan('x.parquet')",
    "SELECT * FROM read_json('x.json')",
    "SELECT * FROM read_text('/etc/passwd')",
    "SELECT * FROM read_blob('x')",
    "SELECT * FROM glob('/*')",
    "SELECT * FROM sniff_csv('x.csv')",
    "SELECT * FROM 'secret.csv'",
    "SELECT * FROM '/etc/passwd'",
    "SELECT * FROM data a JOIN read_csv('x.csv') b ON true",
    "WITH x AS (SELECT * FROM read_csv('a.csv')) SELECT * FROM x",
    "SELECT * FROM data WHERE region IN (SELECT region FROM read_parquet('x'))",
    "SELECT (SELECT count(*) FROM read_csv('a')) FROM data",
    # other tables, schemas, catalogs, system views
    "SELECT * FROM other_table",
    "SELECT * FROM main.data",
    "SELECT * FROM memory.main.data",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM duckdb_settings()",
    "SELECT * FROM duckdb_tables()",
    "SELECT * FROM generate_series(1, 10)",
    "SELECT * FROM range(100)",
    # system information and escape hatches
    "SELECT current_setting('access_mode')",
    "SELECT getenv('PATH')",
    "SELECT * FROM query('SELECT 1')",
    "SELECT $1 FROM data",
    "SELECT * FROM data FOR UPDATE",
    # multiple statements, comment tricks, empties
    "SELECT * FROM data; DROP TABLE data",
    "SELECT 1; SELECT 2",
    "SELECT * FROM data /* ; */; ATTACH 'x'",
    "SELECT 1 -- \n; COPY data TO 'x.csv'",
    "",
    "   ",
    "-- only a comment",
    "SELECT 'unterminated",
    "SELECT " + "1," * 20_000 + "1",
    "SELECT 1\x00; DROP TABLE data",
    "SELECT " + "(" * 500 + "1" + ")" * 500,
]

VALID = [
    "SELECT * FROM data",
    "SELECT * FROM data;",
    "select count(*) as n from data",
    "SELECT region, sum(revenue) AS total FROM data GROUP BY region ORDER BY total DESC",
    "WITH t AS (SELECT region, revenue FROM data) SELECT region, avg(revenue) FROM t GROUP BY 1",
    "SELECT * FROM data WHERE quantity > 2 ORDER BY order_id LIMIT 5",
    "SELECT d.region FROM data AS d WHERE d.revenue > (SELECT avg(revenue) FROM data)",
    "SELECT region FROM data UNION SELECT product FROM data",
    "SELECT strftime(order_date, '%Y-%m') AS month, count(*) FROM data GROUP BY month",
    "SELECT row_number() OVER (PARTITION BY region ORDER BY revenue DESC) AS rn, region FROM data",
    "SELECT approx_count_distinct(product) FROM data",
    "SELECT quantile_cont(revenue, 0.5) AS median FROM data",
    "SELECT a.product, count(*) FROM data a JOIN data b USING (order_id) GROUP BY a.product",
    "SELECT * FROM (SELECT product, revenue FROM data) AS s WHERE revenue > 10",
    "SELECT 1 + 1 AS two",
    "SELECT * FROM data USING SAMPLE 10 ROWS",
    'SELECT "data"."region" FROM "data"',
    "SELECT DATA.region FROM DATA",
]


@dataclass
class Ready:
    """An account with one ready dataset version."""

    account: Account
    version_id: uuid.UUID
    dataset_id: uuid.UUID

    @property
    def tenant_id(self) -> uuid.UUID:
        return uuid.UUID(self.account.tenant_id)


@pytest.fixture
def ready(client: TestClient) -> Ready:
    account = register(client)
    body = upload_ready(client, account, sample("retail_clean.csv"), "retail.csv")
    return Ready(account, uuid.UUID(body["versions"][0]["id"]), uuid.UUID(body["id"]))


@pytest.fixture
def session() -> Iterator[Session]:
    with get_sessionmaker()() as s:
        yield s


@pytest.mark.parametrize("sql", ABUSE, ids=lambda sql: repr(sql[:50]))
def test_abuse_query_is_rejected_by_validator(sql: str) -> None:
    with pytest.raises(QueryRejectedError) as raised:
        validate_sql(sql)
    assert raised.value.message  # a clear message the UI can show
    assert raised.value.status_code == 422 and raised.value.code == "query_rejected"


@pytest.mark.parametrize("sql", ABUSE, ids=lambda sql: repr(sql[:50]))
def test_abuse_query_is_rejected_by_service(session: Session, ready: Ready, sql: str) -> None:
    with pytest.raises(QueryRejectedError):
        SafeQueryService(session).run(ready.tenant_id, ready.version_id, sql)


def test_abuse_suite_is_large_enough() -> None:
    assert len(ABUSE) >= 25 and len(VALID) >= 10


@pytest.mark.parametrize("sql", VALID, ids=lambda sql: repr(sql[:50]))
def test_valid_query_runs(session: Session, ready: Ready, sql: str) -> None:
    result = SafeQueryService(session).run(ready.tenant_id, ready.version_id, sql)
    assert result.columns and result.rows
    assert result.elapsed_ms >= 0


def test_values_and_metadata(session: Session, ready: Ready) -> None:
    result = SafeQueryService(session).run(
        ready.tenant_id,
        ready.version_id,
        "SELECT count(*) AS n, min(order_date) AS first FROM data",
    )
    assert result.columns == ["n", "first"]
    assert result.rows[0][0] == 400 and isinstance(result.rows[0][1], str)  # dates are ISO text
    assert not result.truncated


def test_unicode_identifiers_work(client: TestClient, session: Session) -> None:
    account = register(client)
    body = upload_ready(client, account, sample("retail_arabic_cp1256.csv"), "ar.csv")
    result = SafeQueryService(session).run(
        uuid.UUID(account.tenant_id),
        uuid.UUID(body["versions"][0]["id"]),
        'SELECT "المدينة", count(*) AS n FROM data GROUP BY 1 ORDER BY 1',
    )
    assert sum(row[1] for row in result.rows) == 120


def test_row_limit_truncates_and_is_clamped(session: Session, ready: Ready) -> None:
    service = SafeQueryService(session)
    result = service.run(ready.tenant_id, ready.version_id, "SELECT * FROM data", row_limit=10)
    assert len(result.rows) == 10 and result.truncated and result.row_limit == 10
    exact = service.run(ready.tenant_id, ready.version_id, "SELECT * FROM data", row_limit=400)
    assert len(exact.rows) == 400 and not exact.truncated
    floor = service.run(ready.tenant_id, ready.version_id, "SELECT * FROM data", row_limit=0)
    assert len(floor.rows) == 1 and floor.row_limit == 1
    ceiling = service.run(ready.tenant_id, ready.version_id, "SELECT 1", row_limit=10**9)
    assert ceiling.row_limit == 100_000


def test_default_row_limit_is_1000(session: Session, ready: Ready) -> None:
    result = SafeQueryService(session).run(
        ready.tenant_id,
        ready.version_id,
        "SELECT unnest(range(5000)) AS n",
    )
    assert len(result.rows) == 1000 and result.truncated


def test_timeout_interrupts_runaway_query(session: Session, ready: Ready) -> None:
    sql = "SELECT count(*) FROM data a, data b, data c, data d, data e, data f"
    with pytest.raises(QueryTimeoutError) as raised:
        SafeQueryService(session).run(ready.tenant_id, ready.version_id, sql, timeout_s=0.3)
    assert raised.value.status_code == 408


def test_runtime_errors_become_clear_422(session: Session, ready: Ready) -> None:
    with pytest.raises(QueryFailedError) as raised:
        SafeQueryService(session).run(
            ready.tenant_id, ready.version_id, "SELECT no_such_column FROM data"
        )
    assert "no_such_column" in raised.value.message and "LINE" not in raised.value.message
    with pytest.raises(QueryFailedError):
        SafeQueryService(session).run(
            ready.tenant_id, ready.version_id, "SELECT CAST('abc' AS INTEGER)"
        )


def test_other_tenant_cannot_query_version(
    client: TestClient, session: Session, ready: Ready
) -> None:
    other = register(client, company="Other")
    with pytest.raises(NotFoundError):
        SafeQueryService(session).run(
            uuid.UUID(other.tenant_id), ready.version_id, "SELECT * FROM data"
        )
    with pytest.raises(NotFoundError):
        SafeQueryService(session).run(uuid.uuid4(), ready.version_id, "SELECT * FROM data")
    with pytest.raises(NotFoundError):
        SafeQueryService(session).run(ready.tenant_id, uuid.uuid4(), "SELECT * FROM data")


def test_each_tenant_only_sees_its_own_data(client: TestClient, session: Session) -> None:
    a = register(client)
    b = register(client)
    a_body = upload_ready(client, a, b"v\n1\n2\n3\n", "a.csv")
    b_body = upload_ready(client, b, b"v\n10\n20\n", "b.csv")
    service = SafeQueryService(session)
    ra = service.run(
        uuid.UUID(a.tenant_id), uuid.UUID(a_body["versions"][0]["id"]), "SELECT v FROM data"
    )
    rb = service.run(
        uuid.UUID(b.tenant_id), uuid.UUID(b_body["versions"][0]["id"]), "SELECT v FROM data"
    )
    assert [r[0] for r in ra.rows] == [1, 2, 3] and [r[0] for r in rb.rows] == [10, 20]


def test_deleted_dataset_cannot_be_queried(
    client: TestClient, session: Session, ready: Ready
) -> None:
    assert (
        client.delete(
            f"{API}/datasets/{ready.dataset_id}", headers=ready.account.headers
        ).status_code
        == 204
    )
    with pytest.raises(NotFoundError):
        SafeQueryService(session).run(ready.tenant_id, ready.version_id, "SELECT 1")


def test_not_ready_version_cannot_be_queried(session: Session, ready: Ready) -> None:
    version = session.get(DatasetVersion, ready.version_id)
    assert version is not None
    version.status = VersionStatus.processing.value
    session.commit()
    with pytest.raises(ConflictError):
        SafeQueryService(session).run(ready.tenant_id, ready.version_id, "SELECT 1")
    dataset = session.get(Dataset, ready.dataset_id)
    assert dataset is not None


def test_connection_is_locked_even_if_validation_were_bypassed(
    session: Session, ready: Ready
) -> None:
    """Layer 2: DuckDB itself refuses file access, extensions and config changes."""
    version = session.get(DatasetVersion, ready.version_id)
    assert version is not None and version.parquet_path
    from app.services.storage import get_storage

    parquet = str(get_storage().local_path(version.parquet_path))
    con = SafeQueryService._connect(parquet)
    try:
        assert con.execute("SELECT count(*) FROM data").fetchone() == (400,)
        raw_attacks = [
            f"SELECT * FROM read_parquet('{parquet.replace(chr(92), '/')}')",
            "SELECT * FROM read_csv('x.csv')",
            "SELECT * FROM read_text('x.txt')",
            "SELECT * FROM glob('*')",
            "SELECT * FROM 'x.csv'",
            "COPY data TO 'leak.csv'",
            "ATTACH 'x.db'",
            "LOAD httpfs",
            "INSTALL httpfs",
            "SET enable_external_access = true",
            "SET lock_configuration = false",
            "SET memory_limit = '64GB'",
            "SET autoinstall_known_extensions = true",
            "SET autoload_known_extensions = true",
        ]
        for attack in raw_attacks:
            with pytest.raises(duckdb.Error):
                con.execute(attack)
        flags = dict(
            con.execute(
                "SELECT name, value FROM duckdb_settings() WHERE name IN "
                "('enable_external_access','lock_configuration',"
                "'autoload_known_extensions','autoinstall_known_extensions')"
            ).fetchall()
        )
        assert flags == {
            "enable_external_access": "false",
            "lock_configuration": "true",
            "autoload_known_extensions": "false",
            "autoinstall_known_extensions": "false",
        }
    finally:
        con.close()


def test_connections_are_not_shared(session: Session, ready: Ready) -> None:
    service = SafeQueryService(session)
    service.run(ready.tenant_id, ready.version_id, "SELECT 1")
    with pytest.raises(QueryRejectedError):
        service.run(ready.tenant_id, ready.version_id, "SELECT * FROM t")


def test_clean_makes_values_json_safe() -> None:
    assert _clean(math.nan) is None and _clean(math.inf) is None and _clean(1.5) == 1.5
    assert _clean(Decimal("1.25")) == 1.25 and _clean(date(2024, 1, 2)) == "2024-01-02"
    assert _clean(b"\x01\xff") == "01ff" and _clean(None) is None and _clean(True) is True
    nested: Any = _clean({"a": [1, (2, math.nan)], 3: uuid.UUID(int=1)})
    assert nested == {"a": [1, [2, None]], "3": str(uuid.UUID(int=1))}


def test_short_error_strips_prefix_and_context() -> None:
    message = _short_error(Exception('Binder Error: Referenced column "x" not found\nLINE 1: ...'))
    assert message == 'Referenced column "x" not found'
    assert _short_error(Exception("")) == "The query failed."
