"""Date columns: formats, dd/mm ambiguity, range, granularity and gaps."""

from datetime import date
from statistics import median
from typing import Any

from app.services.profiling.engine import SOURCE_VIEW
from app.services.profiling.parsing import (
    date_sql,
    present_sql,
    quote_ident,
    quote_literal,
    slash_family_parts,
    text_of,
)

ISO_START = r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
TEXTUAL = r"[A-Za-z]{3}"
MAX_DISTINCT_DATES = 200_000


def classify_granularity(median_gap_days: float) -> str:
    """Name the spacing of distinct dates from their median gap in days."""
    bands = (
        (1.5, "daily"),
        (9.0, "weekly"),
        (35.0, "monthly"),
        (100.0, "quarterly"),
        (380.0, "yearly"),
    )
    for limit, name in bands:
        if median_gap_days <= limit:
            return name
    return "irregular"


def _expected_gap(granularity: str) -> float:
    return {"daily": 1, "weekly": 7, "monthly": 31, "quarterly": 92, "yearly": 366}.get(
        granularity, 0
    )


def gap_summary(dates: list[date]) -> dict[str, Any]:
    """Summarise granularity and gaps from sorted distinct dates."""
    if len(dates) < 2:
        return {"granularity": None, "gap_count": 0, "largest_gap_days": 0, "missing_periods": 0}
    gaps = [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]
    granularity = classify_granularity(median(gaps))
    expected = _expected_gap(granularity)
    if expected == 0:
        return {
            "granularity": granularity,
            "gap_count": 0,
            "largest_gap_days": max(gaps),
            "missing_periods": 0,
        }
    threshold = expected * 1.5
    large = [g for g in gaps if g > threshold]
    return {
        "granularity": granularity,
        "gap_count": len(large),
        "largest_gap_days": max(gaps),
        "missing_periods": int(sum(round(g / expected) - 1 for g in large)),
    }


def decide_order(dmy_only: int, mdy_only: int, ambiguous: int) -> tuple[str | None, bool]:
    """Return (day/month order, is_ambiguous) from counts of numeric dates.

    `dmy_only` are dates whose first number exceeds 12, `mdy_only` those whose second number
    does, `ambiguous` fit both orders.
    """
    if dmy_only and mdy_only:
        return "mixed", True
    if dmy_only:
        return "dmy", False
    if mdy_only:
        return "mdy", False
    if ambiguous:
        return None, True
    return None, False


def _format_families(con: Any, ident: str, present: str) -> dict[str, Any]:
    a, b, _ = slash_family_parts(ident)
    text = text_of(ident)
    sql = f"""
        WITH t AS (SELECT {text} AS x, {a} AS a, {b} AS b FROM {SOURCE_VIEW} WHERE {present})
        SELECT
          count(*) FILTER (WHERE regexp_matches(x, {quote_literal(ISO_START)})),
          count(*) FILTER (WHERE a IS NOT NULL AND NOT regexp_matches(x, {quote_literal(ISO_START)})),
          count(*) FILTER (WHERE regexp_matches(x, {quote_literal(TEXTUAL)})),
          count(*) FILTER (WHERE a > 12 AND b <= 12),
          count(*) FILTER (WHERE b > 12 AND a <= 12),
          count(*) FILTER (WHERE a <= 12 AND b <= 12 AND a IS NOT NULL
                           AND NOT regexp_matches(x, {quote_literal(ISO_START)}))
        FROM t
    """  # noqa: E501
    iso, slash, textual, dmy, mdy, ambiguous = (int(v or 0) for v in con.execute(sql).fetchone())
    order, is_ambiguous = decide_order(dmy, mdy, ambiguous)
    families = {k: v for k, v in {"iso": iso, "numeric": slash, "textual": textual}.items() if v}
    return {
        "format_families": families,
        "format_count": len(families),
        "order": order,
        "ambiguous": is_ambiguous,
        "ambiguous_cells": ambiguous if is_ambiguous else 0,
    }


def parsed_expression(ident: str, dtype: str, *, dayfirst: bool = True) -> str:
    """Return the TIMESTAMP expression that reads this column as dates."""
    if dtype in {"DATE", "TIMESTAMP", "TIMESTAMP WITH TIME ZONE"} or dtype.startswith("TIMESTAMP"):
        return f"CAST({ident} AS TIMESTAMP)"
    return date_sql(ident, dayfirst=dayfirst)


def analyze_dates(con: Any, name: str, dtype: str) -> dict[str, Any]:
    """Return date statistics for a column that is (or parses as) dates."""
    ident = quote_ident(name)
    present = present_sql(ident, dtype)
    native = not dtype.startswith("VARCHAR")
    info: dict[str, Any] = {"stored_as": "native" if native else "text"}
    dayfirst = True
    if native:
        info.update({"format_families": {}, "format_count": 1, "order": None, "ambiguous": False})
    else:
        families = _format_families(con, ident, present)
        info.update(families)
        dayfirst = families["order"] != "mdy"
    info["dayfirst"] = dayfirst
    ts = parsed_expression(ident, dtype, dayfirst=dayfirst)
    sql = f"""
        WITH p AS (SELECT {ts} AS ts FROM {SOURCE_VIEW} WHERE {present})
        SELECT count(*), count(ts), min(ts), max(ts),
               count(*) FILTER (WHERE ts IS NOT NULL AND ts <> CAST(CAST(ts AS DATE) AS TIMESTAMP))
        FROM p
    """
    total, parsed, low, high, with_time = con.execute(sql).fetchone()
    info.update(
        {
            "min": low.isoformat() if low else None,
            "max": high.isoformat() if high else None,
            "span_days": (high.date() - low.date()).days if low and high else 0,
            "has_time": bool(with_time),
            "invalid_count": int(total - parsed),
            "conform_share": (parsed / total) if total else 1.0,
        }
    )
    distinct = con.execute(
        f"""
        WITH p AS (SELECT {ts} AS ts FROM {SOURCE_VIEW} WHERE {present})
        SELECT DISTINCT CAST(ts AS DATE) FROM p WHERE ts IS NOT NULL
        ORDER BY 1 LIMIT {MAX_DISTINCT_DATES}
        """
    ).fetchall()
    dates = [r[0] for r in distinct]
    info["distinct_dates"] = len(dates)
    info.update(gap_summary(dates))
    return info


def date_parse_share(con: Any, name: str, dtype: str) -> float:
    """Return the share of non-empty values that parse as dates."""
    ident = quote_ident(name)
    ts = parsed_expression(ident, dtype)
    row = con.execute(
        f"SELECT count(*), count({ts}) FROM {SOURCE_VIEW} WHERE {present_sql(ident, dtype)}"
    ).fetchone()
    return (row[1] / row[0]) if row[0] else 0.0
