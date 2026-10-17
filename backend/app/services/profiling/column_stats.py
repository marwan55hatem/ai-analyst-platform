"""Per-column profiling with DuckDB: type inference, statistics, PII and dates.

Everything is computed by aggregate queries over the Parquet view, so memory use does not
depend on the file size. The LLM never sees or computes any of these numbers (golden rule 1).
"""

from typing import Any

from app.services.ingest import is_identifier_name
from app.services.profiling import pii
from app.services.profiling.dates import analyze_dates, date_parse_share
from app.services.profiling.engine import SOURCE_VIEW
from app.services.profiling.parsing import (
    FALSE_WORDS,
    TRUE_WORDS,
    clean_number_text,
    number_from_clean,
    present_sql,
    quote_ident,
    quote_literal,
)
from app.services.profiling.results import ColumnResult

QUANTILES = (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)
QUANTILE_KEYS = ("p1", "p5", "p25", "p50", "p75", "p95", "p99")
NUMERIC_SHARE = 0.95
MIXED_SHARE = 0.5
DATE_SHARE = 0.9
BOOLEAN_SHARE = 0.95
CATEGORICAL_MAX_DISTINCT = 50
CATEGORICAL_MAX_RATIO = 0.5
CASE_VARIANT_MAX_DISTINCT = 10_000
LEADING_ZEROS_RE2 = "^0[0-9]+$"
FLAG_MAX_DISTINCT = 5  # columns with so few values are flags/codes, not measurements

_INT_TYPES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
)


def _round(value: Any, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def _value_expr(ident: str, is_text: bool) -> str:
    return f"trim({ident})" if is_text else ident


def numeric_stats(
    con: Any, expr: str, present: str, *, detect_outliers: bool = True
) -> dict[str, Any]:
    """Return min/max/mean/median/std, quantiles, skewness and IQR outliers of an expression."""
    quantile_list = "[" + ", ".join(str(q) for q in QUANTILES) + "]"
    row = con.execute(
        f"""
        WITH v AS (SELECT {expr} AS x FROM {SOURCE_VIEW} WHERE {present})
        SELECT count(x), min(x), max(x), avg(x), median(x), stddev_samp(x), skewness(x),
               quantile_cont(x, {quantile_list}),
               count(*) FILTER (WHERE x IS NOT NULL AND x = floor(x))
        FROM v
        """
    ).fetchone()
    count, low, high, mean, med, std, skew, quantiles, integral = row
    stats: dict[str, Any] = {
        "numeric_count": int(count),
        "min": _round(low),
        "max": _round(high),
        "mean": _round(mean),
        "median": _round(med),
        "std": _round(std),
        "skewness": _round(skew),
        "quantiles": (
            dict(zip(QUANTILE_KEYS, (_round(q) for q in quantiles), strict=True))
            if quantiles
            else {}
        ),
        "all_integral": bool(count) and integral == count,
        "outlier_count": 0,
        "outlier_bounds": None,
    }
    if detect_outliers and quantiles and count >= 4:
        q1, q3 = quantiles[2], quantiles[4]
        spread = q3 - q1
        lower, upper = q1 - 1.5 * spread, q3 + 1.5 * spread
        outliers = con.execute(
            f"""
            WITH v AS (SELECT {expr} AS x FROM {SOURCE_VIEW} WHERE {present})
            SELECT count(*) FILTER (WHERE x < {lower!r} OR x > {upper!r}) FROM v
            """
        ).fetchone()[0]
        stats["outlier_count"] = int(outliers)
        stats["outlier_bounds"] = {"lower": _round(lower), "upper": _round(upper)}
    return stats


def _top_values(con: Any, ident: str, is_text: bool, present: str) -> list[dict[str, Any]]:
    value = _value_expr(ident, is_text)
    rows = con.execute(
        f"""
        SELECT CAST({value} AS VARCHAR) AS v, count(*) AS c FROM {SOURCE_VIEW}
        WHERE {present} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10
        """
    ).fetchall()
    return [{"value": r[0], "count": int(r[1])} for r in rows]


def _text_numeric_share(con: Any, ident: str, present: str) -> tuple[int, int, bool]:
    """Return (non-empty values, values that parse as numbers, any has leading zeros)."""
    row = con.execute(
        f"""
        WITH cl AS (SELECT {clean_number_text(ident)} AS s, trim({ident}) AS raw
                    FROM {SOURCE_VIEW} WHERE {present})
        SELECT count(*), count({number_from_clean("s")}),
               count(*) FILTER (WHERE regexp_matches(raw, {quote_literal(LEADING_ZEROS_RE2)}))
        FROM cl
        """
    ).fetchone()
    return int(row[0]), int(row[1]), bool(row[2])


def _boolean_share(con: Any, ident: str, present: str) -> float:
    words = ", ".join(quote_literal(w) for w in (*TRUE_WORDS, *FALSE_WORDS))
    row = con.execute(
        f"""
        SELECT count(*), count(*) FILTER (WHERE lower(trim({ident})) IN ({words}))
        FROM {SOURCE_VIEW} WHERE {present}
        """
    ).fetchone()
    return (row[1] / row[0]) if row[0] else 0.0


def _pii_shares(con: Any, ident: str, present: str) -> dict[str, float]:
    row = con.execute(
        f"""
        WITH t AS (SELECT trim({ident}) AS x FROM {SOURCE_VIEW} WHERE {present})
        SELECT count(*),
          count(*) FILTER (WHERE regexp_matches(x, {quote_literal(pii.EMAIL_RE2)})),
          count(*) FILTER (WHERE regexp_matches(
              regexp_replace(x, '[ ().-]', '', 'g'), {quote_literal(pii.PHONE_RE2)})),
          count(*) FILTER (WHERE regexp_matches(x, {quote_literal(pii.PHONE_FORMATTED_RE2)})),
          count(*) FILTER (WHERE regexp_matches(x, {quote_literal(pii.NATIONAL_ID_RE2)}))
        FROM t
        """
    ).fetchone()
    total = row[0] or 1
    return {
        "email_share": row[1] / total,
        "phone_share": row[2] / total,
        "phone_formatted_share": row[3] / total,
        "national_id_share": row[4] / total,
    }


def _text_quality(
    con: Any, ident: str, present: str, distinct: int, *, categorical: bool
) -> dict[str, Any]:
    """Whitespace and case-variant counts for a text column."""
    padded = con.execute(
        f"SELECT count(*) FILTER (WHERE {ident} <> trim({ident})) "
        f"FROM {SOURCE_VIEW} WHERE {present}"
    ).fetchone()[0]
    info: dict[str, Any] = {"whitespace_count": int(padded)}
    if categorical and distinct <= CASE_VARIANT_MAX_DISTINCT:
        groups, cells = con.execute(
            f"""
            WITH g AS (SELECT lower(trim({ident})) AS k, trim({ident}) AS v, count(*) AS c
                       FROM {SOURCE_VIEW} WHERE {present} GROUP BY 1, 2),
                 m AS (SELECT k, sum(c) AS tot, max(c) AS top FROM g
                       GROUP BY k HAVING count(*) > 1)
            SELECT count(*), coalesce(sum(tot - top), 0) FROM m
            """
        ).fetchone()
        info["case_variant_groups"] = int(groups)
        info["case_variant_cells"] = int(cells)
    return info


def _looks_like_code(con: Any, ident: str, present: str) -> bool:
    """Return True when most values are short space-free tokens with a digit (INV-00042)."""
    share = con.execute(
        f"""
        SELECT count(*) FILTER (WHERE regexp_matches(trim({ident}), '^[A-Za-z0-9._/#-]{{3,40}}$')
                                  AND regexp_matches({ident}, '[0-9]')) * 1.0
               / nullif(count(*), 0)
        FROM {SOURCE_VIEW} WHERE {present}
        """
    ).fetchone()[0]
    return bool(share and share >= 0.8)


def _classify_text(distinct: int, non_null: int) -> str:
    if distinct == 0:
        return "text"
    ratio = distinct / non_null if non_null else 1.0
    if non_null < 20:
        return "categorical" if distinct <= 10 else "text"
    return (
        "categorical"
        if distinct <= CATEGORICAL_MAX_DISTINCT and ratio <= CATEGORICAL_MAX_RATIO
        else "text"
    )


def profile_column(
    con: Any, name: str, original_name: str, dtype: str, total_rows: int
) -> ColumnResult:
    """Profile one column and return its inferred type, statistics and PII kind."""
    ident = quote_ident(name)
    is_text = dtype.startswith("VARCHAR")
    present = present_sql(ident, dtype)
    value = _value_expr(ident, is_text)
    non_null, distinct = con.execute(
        f"""
        SELECT count(*) FILTER (WHERE {present}),
               count(DISTINCT CASE WHEN {present} THEN {value} END)
        FROM {SOURCE_VIEW}
        """
    ).fetchone()
    non_null, distinct = int(non_null), int(distinct)
    nulls = total_rows - non_null
    stats: dict[str, Any] = {
        "row_count": total_rows,
        "non_null_count": non_null,
        "null_count": nulls,
        "null_pct": round(100.0 * nulls / total_rows, 2) if total_rows else 0.0,
        "distinct_count": distinct,
        "distinct_ratio": round(distinct / non_null, 4) if non_null else 0.0,
        "stored_as": "text" if is_text else "native",
        "native_type": dtype,
    }
    shares = (
        _pii_shares(con, ident, present)
        if is_text and non_null
        else {
            "email_share": 0.0,
            "phone_share": 0.0,
            "phone_formatted_share": 0.0,
            "national_id_share": 0.0,
        }
    )
    pii_kind = pii.detect_pii_kind(name, **shares) if (is_text or pii.name_hint(name)) else None
    if pii_kind and non_null:
        # Personal data is never summarised numerically (no min/max of phone numbers).
        inferred = "text" if pii_kind == "email" else "identifier"
        extra = _text_quality(con, ident, present, distinct, categorical=False) if is_text else {}
    else:
        inferred, extra = _infer(con, name, ident, dtype, is_text, present, non_null, distinct)
    stats.update(extra)
    top = _top_values(con, ident, is_text, present)
    if pii_kind:
        top = [
            {"value": pii.mask_value(str(t["value"]), pii_kind), "count": t["count"]} for t in top
        ]
        stats["pii_kind"] = pii_kind
    stats["top_values"] = top
    return ColumnResult(
        name=name,
        original_name=original_name,
        inferred_type=inferred,
        stats=stats,
        pii_kind=pii_kind,
    )


def _infer(
    con: Any,
    name: str,
    ident: str,
    dtype: str,
    is_text: bool,
    present: str,
    non_null: int,
    distinct: int,
) -> tuple[str, dict[str, Any]]:
    """Return (inferred type, extra stats) for a column."""
    if non_null == 0:
        return "text", {}
    if dtype == "BOOLEAN":
        return "boolean", {}
    if dtype.startswith(_INT_TYPES) or dtype.startswith(("DOUBLE", "FLOAT", "DECIMAL", "REAL")):
        is_float = not dtype.startswith(_INT_TYPES)
        expr = (
            f"CASE WHEN isfinite(CAST({ident} AS DOUBLE)) THEN {ident} END" if is_float else ident
        )
        extra = numeric_stats(con, expr, present, detect_outliers=distinct > FLAG_MAX_DISTINCT)
        extra.update({"invalid_count": 0, "conform_share": 1.0})
        if is_identifier_name(name):
            return "identifier", extra
        integer = dtype.startswith(_INT_TYPES)
        return ("integer" if integer else "float"), extra
    if dtype in {"DATE"} or dtype.startswith("TIMESTAMP"):
        return "datetime", analyze_dates(con, name, dtype)
    if not is_text:
        return "text", {}
    return _infer_text(con, name, ident, present, non_null, distinct)


def _infer_text(
    con: Any, name: str, ident: str, present: str, non_null: int, distinct: int
) -> tuple[str, dict[str, Any]]:
    quality = _text_quality
    bool_share = _boolean_share(con, ident, present)
    if bool_share >= BOOLEAN_SHARE:
        extra = quality(con, ident, present, distinct, categorical=False)
        extra.update(
            {"conform_share": bool_share, "invalid_count": round(non_null * (1 - bool_share))}
        )
        return "boolean", extra
    nonempty, parsed, zeros = _text_numeric_share(con, ident, present)
    share = parsed / nonempty if nonempty else 0.0
    if zeros or is_identifier_name(name):
        extra = quality(con, ident, present, distinct, categorical=False)
        extra["leading_zeros"] = zeros
        return "identifier", extra
    if share >= NUMERIC_SHARE:
        extra = numeric_stats(
            con,
            number_from_clean_column(ident),
            present,
            detect_outliers=distinct > FLAG_MAX_DISTINCT,
        )
        extra.update(
            {
                "invalid_count": nonempty - parsed,
                "conform_share": share,
                "needs_cast": True,
                **quality(con, ident, present, distinct, categorical=False),
            }
        )
        return ("integer" if extra["all_integral"] else "float"), extra
    mixed: dict[str, Any] = {}
    if MIXED_SHARE <= share < NUMERIC_SHARE:
        mixed = {"mixed_types": True, "numeric_share": round(share, 4)}
    date_share = date_parse_share(con, name, "VARCHAR")
    if date_share >= DATE_SHARE:
        extra = analyze_dates(con, name, "VARCHAR")
        extra["needs_cast"] = True
        return "datetime", extra
    kind = _classify_text(distinct, non_null)
    if (
        kind == "text"
        and distinct / non_null >= 0.95
        and non_null >= 50
        and _looks_like_code(con, ident, present)
    ):
        kind = "identifier"
    extra = quality(con, ident, present, distinct, categorical=kind == "categorical")
    extra.update(mixed)
    if mixed:
        extra.update({"invalid_count": nonempty - parsed, "conform_share": share})
    return kind, extra


def number_from_clean_column(ident: str) -> str:
    """Return the numeric expression for a messy text column."""
    return number_from_clean(clean_number_text(ident))
