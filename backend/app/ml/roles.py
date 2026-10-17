"""Resolve which column plays which business role (never by hard-coded column names)."""

from dataclasses import dataclass, field
from typing import Any

from app.core.errors import UnprocessableError
from app.services.profiling.parsing import quote_ident

PRIMARY_DATE_HINTS = (
    "order",
    "sale",
    "invoice",
    "transaction",
    "created",
    "الطلب",
    "البيع",
    "الفاتورة",
    "المعاملة",
    "الانشاء",
    "الإنشاء",
)
USED_ROLES = (
    "date",
    "revenue",
    "quantity",
    "price",
    "cost",
    "customer_id",
    "order_id",
    "product",
    "category",
    "region",
    "channel",
)
DIMENSION_ROLES = ("product", "category", "region", "channel")


@dataclass
class RoleMap:
    """The columns chosen for each role in one dataset version.

    `columns` maps a role to a column name. When there is no revenue column but quantity and
    price exist, `derived_revenue` is true and revenue is quantity x price.
    """

    columns: dict[str, str] = field(default_factory=dict)
    date_candidates: list[str] = field(default_factory=list)
    derived_revenue: bool = False
    dayfirst: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def from_profiles(
        cls, profiles: list[Any], column_order: list[str], date_column: str | None = None
    ) -> "RoleMap":
        """Build the map from column profiles; `column_order` is the file's column order."""
        order = {name: i for i, name in enumerate(column_order)}

        def position(profile: Any) -> int:
            return order.get(profile.name, len(order))

        by_role: dict[str, list[Any]] = {}
        for profile in sorted(profiles, key=position):
            by_role.setdefault(profile.semantic_role, []).append(profile)

        columns: dict[str, str] = {}
        for role in USED_ROLES:
            if role == "date":
                continue
            candidates = by_role.get(role, [])
            if candidates:
                best = max(candidates, key=lambda p: (p.role_confidence, -position(p)))
                columns[role] = best.name
        date_profiles = by_role.get("date", [])
        if date_column is not None:
            chosen = next((p for p in profiles if p.name == date_column), None)
            if chosen is None or (
                chosen.inferred_type != "datetime" and chosen.semantic_role != "date"
            ):
                raise UnprocessableError(
                    f"'{date_column}' is not a date column of this dataset.",
                    code="invalid_date_column",
                )
            columns["date"] = chosen.name
        elif date_profiles:
            columns["date"] = _primary_date(date_profiles)
        result = cls(
            columns=columns,
            date_candidates=[p.name for p in date_profiles],
            dayfirst={p.name: bool(p.stats.get("dayfirst", True)) for p in date_profiles},
        )
        if "revenue" not in columns and "quantity" in columns and "price" in columns:
            result.derived_revenue = True
        return result

    def col(self, role: str) -> str | None:
        """Return the column for a role, or None."""
        return self.columns.get(role)

    def has(self, role: str) -> bool:
        """Return whether a role is available (revenue counts when it can be derived)."""
        if role == "revenue":
            return "revenue" in self.columns or self.derived_revenue
        return role in self.columns

    def q(self, role: str, alias: str = "") -> str:
        """Return the quoted column name of a role (it must exist), optionally `alias.`-prefixed."""
        prefix = f"{alias}." if alias else ""
        return prefix + quote_ident(self.columns[role])

    def revenue_expr(self, alias: str = "") -> str | None:
        """Return the SQL expression for revenue (the column, or quantity x price)."""
        if "revenue" in self.columns:
            return self.q("revenue", alias)
        if self.derived_revenue:
            return f"({self.q('quantity', alias)} * {self.q('price', alias)})"
        return None

    def revenue_columns(self) -> list[str]:
        """Return the column names the revenue expression reads."""
        if "revenue" in self.columns:
            return [self.columns["revenue"]]
        if self.derived_revenue:
            return [self.columns["quantity"], self.columns["price"]]
        return []

    def used_columns(self) -> list[str]:
        """Return every column the modules may read, without duplicates."""
        names = list(dict.fromkeys(self.columns.values()))
        return names

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe description."""
        return {
            "columns": dict(self.columns),
            "date_candidates": list(self.date_candidates),
            "derived_revenue": self.derived_revenue,
        }


def _primary_date(candidates: list[Any]) -> str:
    """Prefer names containing order/sale/invoice/transaction/created, else the first."""
    for profile in candidates:
        names = f"{profile.name} {profile.original_name}".lower()
        if any(hint in names for hint in PRIMARY_DATE_HINTS):
            return str(profile.name)
    return str(candidates[0].name)
