"""Structural SQL guards: the personal-data rule and result column names, judged on the parsed tree.

Every query that must be blocked is also run on a real DuckDB table to prove it would have leaked.
"""

from typing import Any

import duckdb
import pytest

from app.agent.pii import PiiPolicy
from app.agent.query_shape import has_oversized_name, makes_dynamic_column_names
from app.services.query import validate_sql


class Profile:
    def __init__(self, name: str, pii: bool) -> None:
        self.name, self.pii_flag = name, pii


POLICY = PiiPolicy.for_profiles(
    [Profile("email", True), Profile("revenue", False), Profile("region", False)],  # type: ignore[list-item]
    allowed=False,
)
SECRET = "a@b.c"


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE data AS SELECT 'a@b.c' AS email, 10 AS revenue, 'N' AS region "
        "UNION ALL SELECT 'x@y.z', 20, 'S'"
    )
    return connection


def leaks(connection: duckdb.DuckDBPyConnection, sql: str) -> bool:
    """Return whether running the SQL puts the secret in the result (values or names)."""
    result = connection.execute(sql)
    names = [d[0] for d in result.description]
    return SECRET in str((names, result.fetchall()))


LEAKING = [
    # aliasing
    "SELECT email AS e FROM data",
    "SELECT t.x FROM (SELECT email AS x FROM data) t",
    "SELECT d.email FROM data AS d",
    "SELECT region AS email, email AS region FROM data",
    # quoting and case
    'SELECT "email" FROM data',
    'SELECT "EMAIL" FROM data',
    "SELECT EmAiL FROM data",
    'SELECT "data"."Email" FROM data',
    "SELECT main.data.email FROM data",
    # comments and whitespace
    "SELECT /* COUNT(email) */ email FROM data",
    "SELECT\n  email -- COUNT(email)\nFROM data",
    "SELECT COUNT(email), email FROM data GROUP BY email",
    # subqueries and CTEs
    "WITH t AS (SELECT email FROM data) SELECT * FROM t",
    "WITH t AS (SELECT email AS x FROM data) SELECT x FROM t",
    "SELECT x FROM (SELECT * FROM (SELECT email AS x FROM data))",
    "SELECT (SELECT MIN(email) FROM data) FROM data",
    "SELECT region FROM data WHERE email IN (SELECT email FROM data)",
    "SELECT region FROM data ORDER BY email",
    "SELECT region, COUNT(*) FROM data GROUP BY region, email",
    # forms that name no column
    "SELECT * FROM data",
    "SELECT * EXCLUDE (revenue) FROM data",
    "SELECT d.* FROM data d",
    "SELECT #1 FROM data",
    "SELECT #1, region FROM data",
    "SELECT COLUMNS('e.*') FROM data",
    "SELECT COLUMNS(c -> c = 'email') FROM data",
    "SELECT d FROM data d",
    "SELECT data FROM data",
    "SELECT to_json(d) FROM data d",
    "SELECT UNNEST(d) FROM data d",
    "SELECT d['email'] FROM data d",
    "SELECT struct_extract(d, 'email') FROM data d",
    # other expressions around the column
    "SELECT list(email) FROM data",
    "SELECT UPPER(email) FROM data",
    "SELECT COUNT(CASE WHEN email = 'a@b.c' THEN email END) FROM data",
    "SELECT {'k': email} FROM data",
    "SELECT [email] FROM data",
]

HARMLESS = [
    "SELECT COUNT(email) FROM data",
    'SELECT COUNT(DISTINCT "Email") AS buyers FROM data',
    "SELECT COUNT(*), COUNT(DISTINCT email) FROM data",
    "SELECT COUNT(email) /* email */ FROM data -- SELECT email",
    "WITH t AS (SELECT revenue FROM data) SELECT SUM(revenue) FROM t",
    "SELECT region, SUM(revenue) FROM data GROUP BY 1 ORDER BY 2 DESC",
    "SELECT SUM(revenue) AS email FROM data",
]


@pytest.mark.parametrize("sql", LEAKING)
def test_every_spelling_that_reaches_a_personal_column_is_blocked(
    sql: str, con: duckdb.DuckDBPyConnection
) -> None:
    clean = validate_sql(sql)
    assert POLICY.blocked_sql_columns(clean) == ["email"], sql
    con.execute(clean)  # still valid DuckDB, so the verdict is not an artefact of a parse error


@pytest.mark.parametrize("sql", HARMLESS)
def test_counting_and_non_personal_queries_pass(sql: str, con: duckdb.DuckDBPyConnection) -> None:
    clean = validate_sql(sql)
    assert POLICY.blocked_sql_columns(clean) == [], sql
    assert not leaks(con, clean), sql


def test_spellings_agree_with_each_other() -> None:
    """The verdict depends on the tree, so formatting variants of one query agree."""
    variants = [
        "select email from data",
        "SELECT   EMAIL\n FROM\tDATA",
        'SELECT "email" FROM "data"',
        "SELECT /* x */ email -- y\n FROM data",
    ]
    assert {tuple(POLICY.blocked_sql_columns(validate_sql(v))) for v in variants} == {("email",)}


def test_the_stated_leak_is_real_without_the_guard(con: duckdb.DuckDBPyConnection) -> None:
    assert leaks(con, "SELECT #1 FROM data")
    assert leaks(con, "SELECT d['email'] FROM data d")


PIVOTS: list[Any] = [
    "SELECT * FROM (PIVOT data ON region USING SUM(revenue))",
    "SELECT * FROM (SELECT revenue, region FROM data) PIVOT (SUM(revenue) FOR region IN ('N', 'S'))",
    "WITH p AS (PIVOT data ON email USING SUM(revenue)) SELECT 1 FROM p",
]


@pytest.mark.parametrize("sql", PIVOTS)
def test_pivot_is_flagged_as_building_names_from_rows(sql: str) -> None:
    assert makes_dynamic_column_names(validate_sql(sql))


def test_pivoted_names_really_come_from_rows(con: duckdb.DuckDBPyConnection) -> None:
    names = [
        d[0]
        for d in con.execute("SELECT * FROM (PIVOT data ON region USING SUM(revenue))").description
    ]
    assert {"N", "S"} <= set(names)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region, SUM(revenue) AS total FROM data GROUP BY region",
        "SELECT SUM(CASE WHEN region = 'N' THEN revenue END) AS north FROM data",
        "SELECT * FROM data UNPIVOT (v FOR k IN (revenue))",
    ],
)
def test_ordinary_queries_keep_static_names(sql: str) -> None:
    assert not makes_dynamic_column_names(validate_sql(sql))


def test_oversized_column_names_are_caught() -> None:
    assert has_oversized_name(["ok", "x" * 129])
    assert not has_oversized_name(["ok", "x" * 128])


STATIC_NAME_QUERIES = [
    "SELECT * FROM data UNPIVOT (v FOR k IN (revenue, revenue))",
    "SELECT COLUMNS('r.*') FROM data",
    "SELECT COLUMNS(c -> c LIKE 'r%') FROM data",
    "SELECT UNNEST({'a': region, 'b': revenue}) FROM data",
    "SELECT UNNEST(STRUCT_PACK(region := region)) FROM data",
    "SELECT map_keys(MAP {region: 1}) FROM data",
    "SELECT * RENAME (revenue AS r2) FROM data",
    "SELECT * FROM data d1 JOIN data d2 USING (region)",
    "SELECT * FROM data POSITIONAL JOIN data d2",
    "SELECT * FROM (SELECT {'x': region} AS s FROM data)",
]


@pytest.mark.parametrize("sql", STATIC_NAME_QUERIES)
def test_other_constructs_take_column_names_from_schema_and_query_text_only(
    sql: str, con: duckdb.DuckDBPyConnection
) -> None:
    """PIVOT is the only DuckDB construct whose output names are row values (probed in Step 8).

    UNPIVOT turns column names into cells (never cells into names); COLUMNS(...) selects by schema;
    struct and map keys inside a value are text of the query, not column names.
    """
    clean = validate_sql(sql)
    assert not makes_dynamic_column_names(clean)
    names = {d[0] for d in con.execute(clean).description}
    cells = {"N", "S", "a@b.c", "x@y.z"}
    assert not names & cells, sql
