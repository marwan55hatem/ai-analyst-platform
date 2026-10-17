"""Stage 1 of role detection: score column names (English and Arabic), types and values."""

import re
from dataclasses import dataclass
from typing import Any

from app.services.profiling.results import NUMERIC_ROLES, NUMERIC_TYPES, ColumnResult

ID_TOKENS = frozenset({"id", "code", "no", "num", "number", "key", "ref", "رقم", "كود", "معرف"})
_AR_PREFIX = re.compile(r"^(?:ال|لل|بال|وال)")

# role -> (strong tokens, weak tokens); a token matches a whole `_`-separated word.
_RAW_TOKENS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "date": (
        frozenset({"date", "timestamp", "datetime", "تاريخ"}),
        frozenset({"time", "day", "month", "year", "created", "ordered", "يوم", "شهر"}),
    ),
    "revenue": (
        frozenset({"revenue", "sales", "turnover", "gmv", "مبيعات", "ايراد", "إيراد", "ايرادات"}),
        frozenset({"amount", "total", "income", "value", "مبلغ", "اجمالي", "إجمالي", "قيمة"}),
    ),
    "quantity": (
        frozenset({"quantity", "qty", "units", "كمية", "الكمية"}),
        frozenset({"count", "pieces", "items", "volume", "عدد", "وحدات"}),
    ),
    "price": (
        frozenset({"price", "rate", "msrp", "سعر", "ثمن", "تسعيرة"}),
        frozenset({"unit", "list"}),
    ),
    "cost": (
        frozenset({"cost", "cogs", "expense", "تكلفة", "تكلفه", "تكاليف", "مصروف", "مصاريف"}),
        frozenset({"spend", "expenses"}),
    ),
    "discount": (
        frozenset({"discount", "rebate", "promo", "خصم", "تخفيض"}),
        frozenset({"off", "coupon", "markdown"}),
    ),
    "customer_id": (
        frozenset({"customer", "client", "buyer", "cust", "عميل", "زبون", "مشتري"}),
        frozenset({"user", "account", "member", "مستخدم"}),
    ),
    "order_id": (
        frozenset({"order", "invoice", "transaction", "receipt", "طلب", "فاتورة", "معاملة"}),
        frozenset({"ticket", "booking", "txn", "إيصال"}),
    ),
    "product": (
        frozenset({"product", "item", "sku", "article", "goods", "منتج", "صنف", "سلعة", "بند"}),
        frozenset({"model", "brand", "ماركة"}),
    ),
    "category": (
        frozenset({"category", "segment", "department", "فئة", "تصنيف", "قسم"}),
        frozenset({"type", "class", "group", "kind", "نوع", "مجموعة"}),
    ),
    "region": (
        frozenset(
            {
                "region",
                "city",
                "country",
                "state",
                "province",
                "governorate",
                "area",
                "territory",
                "منطقة",
                "مدينة",
                "دولة",
                "محافظة",
                "إقليم",
            }
        ),
        frozenset({"location", "branch", "store", "zone", "district", "فرع", "موقع", "مدينه"}),
    ),
    "channel": (
        frozenset({"channel", "platform", "قناة", "منصة"}),
        frozenset({"source", "medium", "مصدر"}),
    ),
    "status": (
        frozenset({"status", "stage", "حالة", "حاله"}),
        frozenset({"state", "phase"}),
    ),
    "email": (frozenset({"email", "mail", "بريد", "ايميل", "إيميل"}), frozenset()),
    "phone": (
        frozenset(
            {"phone", "mobile", "tel", "telephone", "whatsapp", "هاتف", "جوال", "موبايل", "تليفون"}
        ),
        frozenset({"cell", "contact"}),
    ),
    "churn_flag": (
        frozenset({"churn", "churned", "attrition", "تسرب", "انسحاب"}),
        frozenset({"cancelled", "canceled", "left", "lapsed", "ترك"}),
    ),
}
_AR_NORMALIZE = str.maketrans("أإآةى", "اااهي")


def _norm(token: str) -> str:
    """Fold Arabic spelling variants (hamza forms, ة/ه, ى/ي) so patterns match either way."""
    return token.translate(_AR_NORMALIZE)


NAME_TOKENS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    role: (frozenset(map(_norm, strong)), frozenset(map(_norm, weak)))
    for role, (strong, weak) in _RAW_TOKENS.items()
}
CATEGORICAL_ROLES = frozenset({"product", "category", "region", "channel", "status"})
STRONG_SCORE = 0.78
WEAK_SCORE = 0.5
MAX_CONFIDENCE = 0.97
OTHER_CONFIDENCE = 0.5


@dataclass(frozen=True)
class RoleGuess:
    """A detected role with its confidence (0-1)."""

    role: str
    confidence: float
    reason: str


def _tokens(name: str) -> set[str]:
    """Split a normalized column name into words and also add Arabic words without ال."""
    words = {_norm(w) for w in name.lower().split("_") if w}
    return words | {_AR_PREFIX.sub("", w) for w in words if _AR_PREFIX.match(w) and len(w) > 3}


def _name_score(role: str, words: set[str]) -> float:
    strong, weak = NAME_TOKENS[role]
    if words & strong:
        return STRONG_SCORE
    if words & weak:
        return WEAK_SCORE
    return 0.0


def _id_context(words: set[str]) -> bool:
    return bool(words & ID_TOKENS)


def _score(role: str, words: set[str], column: ColumnResult) -> tuple[float, str]:
    """Score one candidate role for a column."""
    stats = column.stats
    ctype = column.inferred_type
    base = _name_score(role, words)
    if role == "date":
        if ctype == "datetime":
            return max(base, 0.55) + (0.2 if base else 0.0), "datetime values"
        return base * 0.35, "name only"
    if role in NUMERIC_ROLES:
        if ctype not in NUMERIC_TYPES:
            return base * 0.2, "name but not numeric"
        bonus = 0.0
        if role == "discount" and stats.get("max") is not None and stats["max"] <= 100:
            bonus = 0.08
        if role == "quantity" and ctype == "integer":
            bonus = 0.1
        return (base + bonus if base else 0.0), "name + numeric"
    if role in {"customer_id", "order_id"}:
        if not base:
            return 0.0, ""
        id_like = _id_context(words) or ctype == "identifier"
        if not id_like:
            return base * 0.55, "name without id marker"
        ratio = float(stats.get("distinct_ratio", 0))
        bonus = (
            0.12
            if (role == "order_id" and ratio >= 0.9) or (role == "customer_id" and ratio < 0.9)
            else 0.0
        )
        return base + bonus + (0.05 if ctype == "identifier" else 0.0), "name + id marker"
    if role in CATEGORICAL_ROLES:
        if not base:
            return 0.0, ""
        if ctype in NUMERIC_TYPES:
            return base * 0.4, "name but numeric"
        if ctype == "datetime":
            return base * 0.2, "name but dates"
        if role == "product" and _id_context(words) and base == STRONG_SCORE:
            return base * 0.85, "product id"
        return base + (0.07 if ctype == "categorical" else 0.0), "name + categorical"
    if role in {"email", "phone"}:
        if column.pii_kind == role:
            return 0.95, "pii pattern"
        return base * 0.5, "name only"
    if role == "churn_flag":
        if not base:
            return 0.0, ""
        flag_like = ctype == "boolean" or (
            ctype in NUMERIC_TYPES and (stats.get("max") or 0) <= 1 and (stats.get("min") or 0) >= 0
        )
        return (base + 0.15 if flag_like else base * 0.5), "name + flag values"
    return 0.0, ""


def detect_role(column: ColumnResult) -> RoleGuess:
    """Return the most likely business role of a column and how sure we are."""
    words = _tokens(column.name) | _tokens(column.original_name.strip().lower().replace(" ", "_"))
    best_role, best_score, best_reason = "other", 0.0, ""
    for role in NAME_TOKENS:
        score, reason = _score(role, words, column)
        if score > best_score + 1e-9:
            best_role, best_score, best_reason = role, score, reason
    if best_score < 0.3:
        return RoleGuess("other", OTHER_CONFIDENCE, "no pattern matched")
    return RoleGuess(best_role, round(min(best_score, MAX_CONFIDENCE), 2), best_reason)


def assign_roles(columns: list[ColumnResult], user_roles: dict[str, str] | None = None) -> None:
    """Set the rule-based role on each column; names in `user_roles` keep the user's choice."""
    for column in columns:
        if user_roles and column.name in user_roles:
            column.semantic_role = user_roles[column.name]
            column.role_confidence = 1.0
            column.role_source = "user"
            continue
        guess = detect_role(column)
        column.semantic_role = guess.role
        column.role_confidence = guess.confidence
        column.role_source = "rule"


def role_summary(columns: list[ColumnResult]) -> dict[str, Any]:
    """Map role -> column names, for convenience in the profile payload."""
    result: dict[str, list[str]] = {}
    for column in columns:
        if column.semantic_role != "other":
            result.setdefault(column.semantic_role, []).append(column.name)
    return result
