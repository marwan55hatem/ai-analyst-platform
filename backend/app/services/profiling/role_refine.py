"""Stage 2 of role detection: ask the LLM about weak guesses, then check it against the profile.

Privacy rules (tested explicitly): only column names and at most five masked sample values per
column are sent, never rows, and never any value of a PII-flagged column. The answer is
validated against the computed profile and discarded when it conflicts (the model never
overrides facts, golden rule 1).
"""

import json
from typing import Any

import structlog

from app.llm.base import LLMClient, LLMError
from app.services.profiling import pii
from app.services.profiling.engine import SOURCE_VIEW
from app.services.profiling.parsing import present_sql, quote_ident
from app.services.profiling.results import NUMERIC_ROLES, NUMERIC_TYPES, ROLES, ColumnResult

logger = structlog.get_logger()

REFINE_BELOW = 0.7
MAX_SAMPLES = 5
MAX_LLM_CONFIDENCE = 0.9
SYSTEM_PROMPT = (
    "You classify spreadsheet columns into business roles. You only see column names and a few "
    "masked example values. Reply with JSON: "
    '{"columns": {"<column name>": {"role": "<role>", "confidence": <0-1>}}}. '
    "Allowed roles: " + ", ".join(ROLES) + ". Use 'other' when unsure."
)


def candidates(columns: list[ColumnResult]) -> list[ColumnResult]:
    """Columns whose rule-based role is weak and was not chosen by the user."""
    return [c for c in columns if c.role_source != "user" and c.role_confidence < REFINE_BELOW]


def collect_samples(
    con: Any, columns: list[ColumnResult], dtypes: dict[str, str]
) -> dict[str, list[str]]:
    """Fetch up to five distinct masked values per column; PII columns get none."""
    samples: dict[str, list[str]] = {}
    for column in columns:
        if column.pii_flag:
            samples[column.name] = []
            continue
        ident = quote_ident(column.name)
        present = present_sql(ident, dtypes.get(column.name, "VARCHAR"))
        rows = con.execute(
            f"""
            SELECT DISTINCT trim(CAST({ident} AS VARCHAR)) FROM {SOURCE_VIEW}
            WHERE {present} LIMIT {MAX_SAMPLES}
            """
        ).fetchall()
        samples[column.name] = [pii.safe_sample(r[0]) for r in rows][:MAX_SAMPLES]
    return samples


def build_request(columns: list[ColumnResult], samples: dict[str, list[str]]) -> str:
    """Build the user message: names and masked samples only."""
    payload = {
        "columns": [
            {
                "name": c.original_name,
                "key": c.name,
                "samples": [] if c.pii_flag else samples.get(c.name, [])[:MAX_SAMPLES],
            }
            for c in columns
        ]
    }
    return json.dumps(payload, ensure_ascii=False)


def is_consistent(role: str, column: ColumnResult) -> bool:
    """Return True when the claimed role does not contradict the column's profile."""
    ctype = column.inferred_type
    if role not in ROLES:
        return False
    if role in NUMERIC_ROLES:
        return ctype in NUMERIC_TYPES
    if role == "date":
        return ctype == "datetime"
    if role in {"email", "phone"}:
        return column.pii_kind == role
    if role == "churn_flag":
        low, high = column.stats.get("min"), column.stats.get("max")
        return ctype == "boolean" or (
            ctype in NUMERIC_TYPES
            and low is not None
            and high is not None
            and low >= 0
            and high <= 1
        )
    if role in {"customer_id", "order_id"}:
        return ctype in {"identifier", "integer", "text", "categorical"}
    if role in {"product", "category", "region", "channel", "status"}:
        return ctype in {"categorical", "text", "identifier", "boolean"}
    return True


def apply_answer(answer: dict[str, Any], columns: list[ColumnResult]) -> int:
    """Apply valid, consistent answers to the columns; return how many were applied."""
    by_key = {c.name: c for c in columns}
    entries = answer.get("columns")
    if not isinstance(entries, dict):
        return 0
    applied = 0
    for key, entry in entries.items():
        column = by_key.get(key)
        if column is None or column.role_source == "user" or not isinstance(entry, dict):
            continue
        role = entry.get("role")
        raw = entry.get("confidence", 0.0)
        confidence = float(raw) if isinstance(raw, int | float) else 0.0
        if not isinstance(role, str) or role == "other" or not is_consistent(role, column):
            continue
        confidence = min(max(confidence, 0.0), MAX_LLM_CONFIDENCE)
        if confidence <= column.role_confidence:
            continue
        column.semantic_role = role
        column.role_confidence = round(confidence, 2)
        column.role_source = "llm"
        applied += 1
    return applied


def refine_roles(
    con: Any, columns: list[ColumnResult], dtypes: dict[str, str], client: LLMClient | None
) -> int:
    """Refine weak roles through the LLM; return the number of roles changed.

    Any LLM failure or malformed answer leaves the rule-based roles untouched.
    """
    if client is None:
        return 0
    weak = candidates(columns)
    if not weak:
        return 0
    samples = collect_samples(con, weak, dtypes)
    try:
        answer = client.complete_json(SYSTEM_PROMPT, build_request(weak, samples))
    except (LLMError, ValueError, TypeError):
        logger.warning("role_refinement_failed")
        return 0
    return apply_answer(answer, weak)
