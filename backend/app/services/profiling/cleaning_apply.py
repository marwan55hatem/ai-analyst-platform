"""Apply chosen cleaning fixes to a Parquet file, writing a new file (the source is never changed).

Runs in the worker with our own SQL over a memory-limited DuckDB connection. Fixes run in a
fixed order (trim, cast, parse dates, case, dedupe, nulls, outlier flags); each stage is a
view over the previous one, so the data is streamed rather than loaded into pandas.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.profiling.cleaning_plan import FIX_ORDER, KEEP
from app.services.profiling.engine import SOURCE_VIEW, open_parquet, scalar, schema_of
from app.services.profiling.parsing import (
    bool_sql,
    date_sql,
    number_sql,
    present_sql,
    quote_ident,
    quote_literal,
)

ROW_GROUP_SIZE = 100_000
INTEGER_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT")


@dataclass
class AppliedFix:
    """The effect of one applied fix."""

    fix_id: str
    description: str
    rows_affected: int


@dataclass
class ApplyResult:
    """Outcome of applying a set of fixes."""

    row_count: int
    columns: list[str]
    new_columns: list[str]
    applied: list[AppliedFix] = field(default_factory=list)


class _Pipeline:
    """Chains stage views over the source and counts what each fix touches."""

    def __init__(self, con: Any) -> None:
        self.con = con
        self.current = SOURCE_VIEW
        self._stage = 0
        self.applied: list[AppliedFix] = []

    def dtypes(self) -> dict[str, str]:
        return dict(schema_of(self.con, self.current))

    def stage(self, select_sql: str) -> None:
        self._stage += 1
        name = f"stage_{self._stage}"
        self.con.execute(f"CREATE VIEW {name} AS {select_sql}")
        self.current = name

    def count(self, where: str | None = None, source: str | None = None) -> int:
        clause = f" WHERE {where}" if where else ""
        return int(scalar(self.con, f"SELECT count(*) FROM {source or self.current}{clause}"))

    def log(self, fix: dict[str, Any], rows: int) -> None:
        self.applied.append(AppliedFix(fix["id"], fix["description"], rows))

    # ---- fix types -------------------------------------------------------------------------

    def trim(self, fix: dict[str, Any], _strategy: str | None) -> None:
        column = fix["columns"][0]
        ident = quote_ident(column)
        rows = self.count(f"{ident} IS NOT NULL AND {ident} <> trim({ident})")
        self.stage(f"SELECT * REPLACE (trim({ident}) AS {ident}) FROM {self.current}")
        self.log(fix, rows)

    def cast(self, fix: dict[str, Any], _strategy: str | None) -> None:
        column = fix["columns"][0]
        ident = quote_ident(column)
        target = fix["params"]["target"]
        if target == "boolean":
            expr = bool_sql(ident)
        elif target == "integer":
            expr = f"CAST(round({number_sql(ident)}) AS BIGINT)"
        else:
            expr = number_sql(ident)
        rows = self.count(present_sql(ident, "VARCHAR"))
        self.stage(f"SELECT * REPLACE ({expr} AS {ident}) FROM {self.current}")
        self.log(fix, rows)

    def parse_dates(self, fix: dict[str, Any], strategy: str | None) -> None:
        column = fix["columns"][0]
        ident = quote_ident(column)
        params = fix["params"]
        dayfirst = bool(params.get("dayfirst", True))
        if strategy == "monthfirst":
            dayfirst = False
        elif strategy == "dayfirst":
            dayfirst = True
        expr = date_sql(ident, dayfirst=dayfirst)
        if params.get("target") != "timestamp":
            expr = f"CAST({expr} AS DATE)"
        rows = self.count(present_sql(ident, "VARCHAR"))
        self.stage(f"SELECT * REPLACE ({expr} AS {ident}) FROM {self.current}")
        self.log(fix, rows)

    def case(self, fix: dict[str, Any], _strategy: str | None) -> None:
        column = fix["columns"][0]
        ident = quote_ident(column)
        table = f"casemap_{self._stage + 1}"
        self.con.execute(
            f"""
            CREATE TEMP TABLE {table} AS
            SELECT lower(v) AS __k, arg_max(v, cnt) AS __canon
            FROM (SELECT trim({ident}) AS v, count(*) AS cnt FROM {self.current}
                  WHERE {ident} IS NOT NULL GROUP BY 1)
            GROUP BY lower(v)
            """
        )
        rows = int(
            scalar(
                self.con,
                f"""
                SELECT count(*) FROM {self.current} p
                JOIN {table} m ON lower(trim(p.{ident})) = m.__k
                WHERE trim(p.{ident}) <> m.__canon
                """,
            )
        )
        self.stage(
            f"""
            SELECT * EXCLUDE (__k, __canon) REPLACE (COALESCE(__canon, {ident}) AS {ident})
            FROM {self.current} p LEFT JOIN {table} m ON lower(trim(p.{ident})) = m.__k
            """
        )
        self.log(fix, rows)

    def dedupe(self, fix: dict[str, Any], _strategy: str | None) -> None:
        before = self.count()
        cols = ", ".join(quote_ident(n) for n in self.dtypes())
        self.stage(
            f"""
            WITH n AS (SELECT *, row_number() OVER () AS __rn FROM {self.current}),
                 g AS (SELECT min(__rn) AS __rn, {cols} FROM n GROUP BY {cols})
            SELECT {cols} FROM g ORDER BY __rn
            """
        )
        self.log(fix, before - self.count())

    def nulls(self, fix: dict[str, Any], strategy: str | None) -> None:
        strategy = strategy or fix.get("default_strategy") or KEEP
        if strategy == KEEP:
            return
        column = fix["columns"][0]
        ident = quote_ident(column)
        dtype = self.dtypes()[column]
        text = dtype.startswith("VARCHAR")
        value = f"NULLIF(trim({ident}), '')" if text else ident
        missing = self.count(f"{value} IS NULL")
        if strategy == "drop_rows":
            self.stage(f"SELECT * FROM {self.current} WHERE {value} IS NOT NULL")
        elif strategy == "impute_median":
            filled = f"COALESCE({value}, median({value}) OVER ())"
            if dtype.startswith(INTEGER_TYPES):
                filled = f"CAST(round({filled}) AS {dtype})"
            self.stage(f"SELECT * REPLACE ({filled} AS {ident}) FROM {self.current}")
        elif strategy == "impute_mode":
            self.stage(
                f"SELECT * REPLACE (COALESCE({value}, mode({value}) OVER ()) AS {ident}) "
                f"FROM {self.current}"
            )
        else:
            raise ValueError(f"Unknown null strategy: {strategy}")
        self.log(fix, missing)

    def flag_outliers(self, fix: dict[str, Any], _strategy: str | None) -> None:
        column = fix["columns"][0]
        ident = quote_ident(column)
        dtype = self.dtypes()[column]
        value = number_sql(ident) if dtype.startswith("VARCHAR") else ident
        flag = f"{column}_is_outlier"
        taken = set(self.dtypes())
        suffix = 2
        while flag in taken:
            flag = f"{column}_is_outlier_{suffix}"
            suffix += 1
        flag_ident = quote_ident(flag)
        self.stage(
            f"""
            SELECT * EXCLUDE (__q1, __q3),
                   COALESCE(__v < __q1 - 1.5 * (__q3 - __q1)
                            OR __v > __q3 + 1.5 * (__q3 - __q1), FALSE) AS {flag_ident}
            FROM (SELECT *, {value} AS __v,
                         quantile_cont({value}, 0.25) OVER () AS __q1,
                         quantile_cont({value}, 0.75) OVER () AS __q3
                  FROM {self.current})
            """
        )
        self.stage(f"SELECT * EXCLUDE (__v) FROM {self.current}")
        self.log(fix, self.count(flag_ident))


def apply_fixes(
    src: Path, dst: Path, plan: list[dict[str, Any]], selection: dict[str, str | None]
) -> ApplyResult:
    """Apply the selected fixes (ids from `plan`, optional strategy each) and write `dst`."""
    chosen = [f for f in plan if f["id"] in selection]
    ordered = sorted(chosen, key=lambda f: (FIX_ORDER.index(f["type"]), f["id"]))
    tmp = dst.with_name(dst.name + ".tmp")
    with open_parquet(src) as con:
        source_columns = [n for n, _ in schema_of(con)]
        pipeline = _Pipeline(con)
        for fix in ordered:
            getattr(pipeline, fix["type"])(fix, selection[fix["id"]])
        try:
            con.execute(
                f"COPY (SELECT * FROM {pipeline.current}) TO {quote_literal(str(tmp))} "
                f"(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {ROW_GROUP_SIZE})"
            )
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        columns = [n for n, _ in schema_of(con, pipeline.current)]
        rows = pipeline.count()
    os.replace(tmp, dst)
    return ApplyResult(
        row_count=rows,
        columns=columns,
        new_columns=[c for c in columns if c not in source_columns],
        applied=pipeline.applied,
    )
