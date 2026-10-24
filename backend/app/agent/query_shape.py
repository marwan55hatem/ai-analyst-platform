"""Structural rules for agent SQL that look at the parsed tree, never at the SQL text.

Result column names must come from the query, not from the data: DuckDB's `PIVOT` turns the
values of a column into column names, which would carry row-level values past the row cap, the
personal-data guard and the evidence rules that treat names as harmless labels.
"""

import sqlglot
from sqlglot import exp

MAX_COLUMN_NAME_CHARS = 128
DYNAMIC_NAMES_MESSAGE = (
    "PIVOT is not allowed: it would turn row values into column names. Use GROUP BY with "
    "conditional aggregation (SUM(CASE WHEN ...)) and name the columns yourself."
)
LONG_NAME_MESSAGE = "A result column name is longer than {limit} characters; give it a short alias."


def makes_dynamic_column_names(sql: str) -> bool:
    """Return True when the parsed query builds column names from row values (PIVOT)."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    return any(not pivot.args.get("unpivot") for pivot in tree.find_all(exp.Pivot))


def has_oversized_name(columns: list[str]) -> bool:
    """Return True when a result column name is long enough to hide data."""
    return any(len(name) > MAX_COLUMN_NAME_CHARS for name in columns)
