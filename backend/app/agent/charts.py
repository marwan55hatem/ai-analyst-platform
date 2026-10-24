"""Turn a query result into a chart spec; the chart type follows the shape of the data."""

import re
from datetime import date, datetime
from typing import Any

from app.services.query import QueryResult

MAX_POINTS = 500
MAX_BAR_CATEGORIES = 20
NUMERIC_TYPES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
)
TEMPORAL_TYPES = ("DATE", "TIMESTAMP")
ISO_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?([ T].*)?$")


def _is_numeric(column_type: str) -> bool:
    return column_type.upper().startswith(NUMERIC_TYPES) or column_type.upper().startswith("UBIG")


def _is_temporal(column_type: str, values: list[Any]) -> bool:
    if column_type.upper().startswith(TEMPORAL_TYPES):
        return True
    present = [v for v in values if v is not None]
    return bool(present) and all(
        isinstance(v, (date, datetime)) or (isinstance(v, str) and ISO_DATE.match(v))
        for v in present
    )


def chart_from_result(result: QueryResult, title: str) -> dict[str, Any]:
    """Return a chart spec: kpi (one value), line (date x), bar (few categories), scatter or table.

    The spec carries the data points so the frontend needs no second query.
    """
    columns, types, rows = result.columns, result.column_types, result.rows[:MAX_POINTS]
    numeric = [i for i, t in enumerate(types) if _is_numeric(t)]
    base = {"title": title[:200], "columns": columns, "truncated": result.truncated}
    if len(rows) == 1 and len(numeric) == 1:
        index = numeric[0]
        return {**base, "type": "kpi", "label": columns[index], "value": rows[0][index]}
    if not rows or not numeric:
        return {**base, "type": "table", "rows": rows}
    first_values = [row[0] for row in rows]
    y_columns = [columns[i] for i in numeric if i != 0]
    if _is_temporal(types[0], first_values) and y_columns:
        return {
            **base,
            "type": "line",
            "x": columns[0],
            "y": y_columns,
            "data": _records(rows, columns),
        }
    if 0 not in numeric and y_columns and len(rows) <= MAX_BAR_CATEGORIES:
        return {
            **base,
            "type": "bar",
            "x": columns[0],
            "y": y_columns,
            "data": _records(rows, columns),
        }
    if len(numeric) >= 2:
        x, y = columns[numeric[0]], columns[numeric[1]]
        return {**base, "type": "scatter", "x": x, "y": [y], "data": _records(rows, columns)}
    return {**base, "type": "table", "rows": rows}


def _records(rows: list[list[Any]], columns: list[str]) -> list[dict[str, Any]]:
    return [dict(zip(columns, row, strict=False)) for row in rows]
