"""Tools that read the dataset: overview, SQL and charts."""

from typing import Any

from pydantic import Field

from app.agent.charts import chart_from_result
from app.agent.pii import BLOCKED_MESSAGE, PiiPolicy
from app.agent.query_shape import (
    DYNAMIC_NAMES_MESSAGE,
    LONG_NAME_MESSAGE,
    MAX_COLUMN_NAME_CHARS,
    has_oversized_name,
    makes_dynamic_column_names,
)
from app.agent.tools.base import AgentContext, Tool, ToolArgs, ToolResult, fit_rows
from app.core.errors import AppError
from app.db.repository import ColumnProfileRepository
from app.services.analysis import capabilities_payload
from app.services.query import QueryResult, SafeQueryService, validate_sql

SQL_ROW_CAP = 1000
SQL_TIMEOUT_S = 10.0
ROWS_FOR_MODEL = 50


class NoArgs(ToolArgs):
    """The tool takes no arguments."""


class SqlArgs(ToolArgs):
    """One read-only SELECT over the table `data`."""

    sql: str = Field(
        min_length=1,
        max_length=20_000,
        description="One DuckDB SELECT over the table `data` (CTEs allowed). Max 1000 rows back.",
    )


class ChartArgs(SqlArgs):
    """A SELECT whose result becomes a chart."""

    title: str = Field(min_length=1, max_length=200, description="Chart title.")


def _policy(ctx: AgentContext) -> PiiPolicy:
    profiles = ColumnProfileRepository(ctx.db, ctx.tenant_id).for_version(ctx.version.id)
    return PiiPolicy.for_profiles(profiles, ctx.allow_pii)


def dataset_overview(ctx: AgentContext, _: NoArgs) -> ToolResult:
    """Columns, roles, types, quality score and which analyses can run."""
    profiles = ColumnProfileRepository(ctx.db, ctx.tenant_id).for_version(ctx.version.id)
    columns = [
        {
            "name": p.name,
            "type": p.inferred_type,
            "role": p.semantic_role,
            "personal_data": p.pii_flag,
            "hidden": p.pii_flag and not ctx.allow_pii,
        }
        for p in profiles
    ]
    try:
        caps = capabilities_payload(ctx.db, ctx.tenant_id, ctx.version)
        modules = caps.get("modules", {})
    except AppError as exc:
        modules = {"error": exc.message}
    content = {
        "dataset": ctx.dataset.name,
        "version": ctx.version.version,
        "rows": ctx.version.row_count,
        "quality_score": ctx.version.quality_score,
        "table": "data",
        "columns": columns,
        "analyses": modules,
    }
    return ToolResult(
        ok=True,
        content=content,
        evidence={"version_id": str(ctx.version.id), "columns": columns},
        summary=f"{len(columns)} columns, {ctx.version.row_count} rows",
    )


def _run_query(ctx: AgentContext, sql: str) -> QueryResult | ToolResult:
    try:
        clean = validate_sql(sql)
    except AppError as exc:
        return ToolResult.error(exc.message, code=exc.code, sql=sql)
    if makes_dynamic_column_names(clean):
        return ToolResult.error(DYNAMIC_NAMES_MESSAGE, code="dynamic_columns_blocked", sql=sql)
    blocked = _policy(ctx).blocked_sql_columns(clean)
    if blocked:
        message = BLOCKED_MESSAGE.format(columns=", ".join(blocked))
        return ToolResult.error(message, code="personal_data_blocked", sql=sql, columns=blocked)
    try:
        result = SafeQueryService(ctx.db).run(
            ctx.tenant_id, ctx.version.id, clean, row_limit=SQL_ROW_CAP, timeout_s=SQL_TIMEOUT_S
        )
    except AppError as exc:
        return ToolResult.error(exc.message, code=exc.code, sql=sql)
    if has_oversized_name(result.columns):  # second layer behind the tree check above
        message = LONG_NAME_MESSAGE.format(limit=MAX_COLUMN_NAME_CHARS)
        return ToolResult.error(message, code="column_name_too_long", sql=sql)
    return result


def run_sql(ctx: AgentContext, args: SqlArgs) -> ToolResult:
    """Run one validated SELECT and return up to 1000 rows (50 shown to the model)."""
    result = _run_query(ctx, args.sql)
    if isinstance(result, ToolResult):
        return result
    shown = result.rows[:ROWS_FOR_MODEL]
    content: dict[str, Any] = {
        "columns": result.columns,
        "rows": shown,
        "row_count": len(result.rows),
        "truncated_at_cap": result.truncated,
    }
    if len(result.rows) > len(shown):
        content["note"] = f"only the first {len(shown)} of {len(result.rows)} rows are shown"
    cut = fit_rows(content, shown, ctx.settings.agent_tool_output_chars)
    if cut:
        content["note"] = cut
    evidence = {
        "sql": args.sql,
        "columns": result.columns,
        "column_types": result.column_types,
        "rows": result.rows,
        "row_count": len(result.rows),
        "truncated": result.truncated,
        "elapsed_ms": round(result.elapsed_ms, 1),
        "version_id": str(ctx.version.id),
    }
    return ToolResult(
        ok=True, content=content, evidence=evidence, summary=f"{len(result.rows)} rows"
    )


def make_chart(ctx: AgentContext, args: ChartArgs) -> ToolResult:
    """Run a SELECT and turn its result into a chart spec."""
    result = _run_query(ctx, args.sql)
    if isinstance(result, ToolResult):
        return result
    spec = chart_from_result(result, args.title)
    content = {"chart_type": spec["type"], "points": len(result.rows), "title": args.title}
    evidence = {"sql": args.sql, "columns": result.columns, "rows": result.rows[:200]}
    return ToolResult(
        ok=True, content=content, evidence=evidence, summary=f"{spec['type']} chart", chart=spec
    )


TOOLS = [
    Tool(
        "get_dataset_overview",
        "Columns (name, type, business role, personal-data flag), row count, quality score and "
        "which analyses can run. Call this first when you do not know the columns.",
        NoArgs,
        dataset_overview,
    ),
    Tool(
        "run_sql",
        "Run one read-only DuckDB SELECT on the table `data` (this dataset only). Returns at most "
        "1000 rows; 10 second limit. Personal-data columns can only be counted.",
        SqlArgs,
        run_sql,
    ),
    Tool(
        "make_chart",
        "Run a SELECT and turn the result into a chart (line for dates, bar for categories, "
        "scatter for two numbers, kpi for one value).",
        ChartArgs,
        make_chart,
    ),
]
