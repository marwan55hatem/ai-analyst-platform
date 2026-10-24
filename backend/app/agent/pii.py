"""Personal-data rules for agent tools.

Columns flagged as personal data in the profile are blocked from SQL results and from the data sent
to the sandbox, unless the company opted in (`business_profiles.allow_pii`). In SQL they may still
be counted (`COUNT(col)`, `COUNT(DISTINCT col)`), which reveals no values.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from app.models.profile import ColumnProfile

BLOCKED_MESSAGE = (
    "These columns hold personal data and are hidden from answers: {columns}. "
    "Use COUNT(DISTINCT column) to count them, or ask an owner to allow personal data in the "
    "company profile."
)


@dataclass(frozen=True)
class PiiPolicy:
    """The personal-data columns of one version and whether they may be shown."""

    columns: frozenset[str]
    allowed: bool

    @classmethod
    def for_profiles(cls, profiles: list[ColumnProfile], allowed: bool) -> "PiiPolicy":
        """Build the policy from column profiles."""
        return cls(frozenset(p.name.lower() for p in profiles if p.pii_flag), allowed)

    def blocked_sql_columns(self, sql: str) -> list[str]:
        """Return personal-data columns the (already validated) SQL would expose."""
        if self.allowed or not self.columns:
            return []
        tree = sqlglot.parse_one(sql, read="duckdb")
        exposed: set[str] = set()
        for column in tree.find_all(exp.Column):
            name = column.name.lower()
            if name in self.columns and not _inside_count(column):
                exposed.add(name)
        if _selects_whole_rows(tree):
            exposed |= set(self.columns)
        return sorted(exposed)


def _selects_whole_rows(tree: exp.Expr) -> bool:
    """Return True for anything that expands to columns the guard cannot name.

    That is a bare star (`*`, `t.*`), a positional reference (`#1`), DuckDB's `COLUMNS(...)`
    selector (a pattern or lambda picks the columns at run time) and a reference to a table, alias
    or CTE name, which DuckDB returns as the whole row packed in a struct (`SELECT d FROM data d`).
    """
    if any(_is_bare_star(star) for star in tree.find_all(exp.Star)):
        return True
    if any(True for _ in tree.find_all(exp.PositionalColumn)):  # DuckDB `#1` names no column
        return True
    for func in tree.find_all(exp.Func):
        if isinstance(func, exp.Columns) or func.sql_name().lower() == "columns":
            return True
    sources = {t.name.lower() for t in tree.find_all(exp.Table)}
    sources |= {a.name.lower() for a in tree.find_all(exp.TableAlias) if a.name}
    return any(not c.table and c.name.lower() in sources for c in tree.find_all(exp.Column))


def _inside_count(node: exp.Expr) -> bool:
    """Return True only for `COUNT(col)` and `COUNT(DISTINCT col)` (not `COUNT(CASE ...)`)."""
    parent = node.parent
    if isinstance(parent, exp.Distinct):
        parent = parent.parent
    return isinstance(parent, exp.Count)


def _is_bare_star(star: exp.Star) -> bool:
    """`SELECT *` or `t.*` exposes every column; `COUNT(*)` does not."""
    return not isinstance(star.parent, exp.Count)
