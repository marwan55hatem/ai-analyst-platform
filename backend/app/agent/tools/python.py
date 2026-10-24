"""run_python: model-written code runs in the isolated sandbox on this dataset's role columns."""

from pydantic import Field

from app.agent.code_check import check_code
from app.agent.sandbox_client import SandboxError, build_data
from app.agent.tools.base import AgentContext, Tool, ToolArgs, ToolResult
from app.core.errors import AppError


class PythonArgs(ToolArgs):
    """Code that reads `df` and stores its answer in `result`."""

    code: str = Field(
        min_length=1,
        max_length=20_000,
        description=(
            "Python using pandas as pd and numpy as np on the DataFrame `df` (role columns only). "
            "Assign the answer to `result` (JSON-serialisable) and optionally `chart` (a spec)."
        ),
    )


def run_python(ctx: AgentContext, args: PythonArgs) -> ToolResult:
    """Check the code, package the data and run it in the sandbox."""
    problems = check_code(args.code)
    if problems:
        return ToolResult.error(
            "The code was refused by the safety check: " + "; ".join(problems[:5]),
            code="code_rejected",
            code_text=args.code,
            problems=problems,
        )
    if ctx.sandbox is None:
        return ToolResult.error("The Python sandbox is not configured.", code="sandbox_unavailable")
    try:
        data = build_data(ctx.db, ctx.tenant_id, ctx.version, ctx.settings, allow_pii=ctx.allow_pii)
    except AppError as exc:
        return ToolResult.error(exc.message, code=exc.code)
    try:
        response = ctx.sandbox.run(args.code, data)
    except SandboxError as exc:
        return ToolResult.error(exc.message, code=exc.code, code_text=args.code)
    notes = list(data.warnings)
    if data.removed_pii:
        notes.append("Personal-data columns were left out: " + ", ".join(data.removed_pii))
    if data.pseudonymised:
        notes.append("Customer ids are pseudonyms: " + ", ".join(data.pseudonymised))
    evidence = {
        "code": args.code,
        "columns": data.columns,
        "rows_sent": data.rows,
        "total_rows": data.total_rows,
        "notes": notes,
        "sandbox": {k: response.get(k) for k in ("ok", "kind", "error", "duration_ms")},
        "stdout": response.get("stdout", ""),
        "result": response.get("result"),
    }
    if not response.get("ok"):
        message = str(response.get("error") or "the code failed")
        kind = str(response.get("kind") or "error")
        result = ToolResult.error(f"{message} ({kind})", code=f"python_{kind}", code_text=args.code)
        result.evidence.update(evidence)
        return result
    content = {
        "result": response.get("result"),
        "stdout": response.get("stdout", "")[:4000],
        "rows_analysed": data.rows,
        "notes": notes,
    }
    caveats = [{"kind": "sampled_data", "message": w} for w in data.warnings]
    chart = response.get("chart") if isinstance(response.get("chart"), dict) else None
    return ToolResult(
        ok=True,
        content=content,
        evidence=evidence,
        summary=f"ran in {response.get('duration_ms')} ms",
        chart=chart,
        caveats=caveats,
    )


TOOLS = [
    Tool(
        "run_python",
        "Run Python (pandas, numpy, scipy, statsmodels, scikit-learn) in an isolated sandbox on "
        "this dataset's role columns as `df`. No files, no network, 15 second limit. Use it only "
        "when SQL cannot do the calculation.",
        PythonArgs,
        run_python,
    )
]
