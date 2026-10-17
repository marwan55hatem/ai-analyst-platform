"""Detect personal data (emails, phones, national-ID-like numbers) and mask sample values."""

import re

EMAIL_RE2 = r"^[^@\s]+@[^@\s]+[.][^@\s]+$"
# 8-15 digits once spaces, dots, dashes and brackets are removed, optional leading +.
PHONE_RE2 = r"^[+]?[0-9]{8,15}$"
PHONE_FORMATTED_RE2 = r"^([+][0-9]|[(][0-9]{2,4}[)]|[0-9]{3}[ .-][0-9]{3}[ .-][0-9]{4}$)"
NATIONAL_ID_RE2 = r"^([0-9]{14}|[0-9]{3}-[0-9]{2}-[0-9]{4})$"

EMAIL_TOKENS = frozenset({"email", "mail", "بريد", "ايميل", "إيميل"})
PHONE_TOKENS = frozenset(
    {"phone", "mobile", "tel", "telephone", "cell", "whatsapp", "هاتف", "جوال", "موبايل", "تليفون"}
)
NATIONAL_ID_TOKENS = frozenset({"ssn", "nid", "passport", "iqama", "هوية", "جواز", "قومي"})

_EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_IN_TEXT = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_LONG_DIGITS = re.compile(r"\d{9,}")
MAX_SAMPLE_CHARS = 40


def name_hint(name: str) -> str | None:
    """Return the PII kind suggested by the column name alone, if any."""
    tokens = set(name.lower().split("_"))
    if tokens & EMAIL_TOKENS:
        return "email"
    if tokens & PHONE_TOKENS:
        return "phone"
    if tokens & NATIONAL_ID_TOKENS or {"national", "id"} <= tokens:
        return "national_id"
    return None


def detect_pii_kind(
    name: str,
    *,
    email_share: float,
    phone_share: float,
    phone_formatted_share: float,
    national_id_share: float,
) -> str | None:
    """Decide whether a column holds personal data.

    A column is flagged when most values look like an email, phone or national id, or when its
    name says so (conservative: a flagged column never sends values anywhere).
    """
    if email_share >= 0.6:
        return "email"
    if national_id_share >= 0.6:
        return "national_id"
    hint = name_hint(name)
    if phone_share >= 0.6 and (hint == "phone" or phone_formatted_share >= 0.5):
        return "phone"
    return hint


def mask_email(value: str) -> str:
    """Keep the first letter of each part: j***@e***.com."""
    local, _, domain = value.partition("@")
    host, _, tld = domain.rpartition(".")
    return f"{local[:1]}***@{host[:1]}***.{tld}" if domain else f"{value[:1]}***"


def mask_digits(value: str) -> str:
    """Hide all but the last two digits of a number-like value."""
    digits = [i for i, c in enumerate(value) if c.isdigit()]
    keep = set(digits[-2:])
    return "".join("*" if c.isdigit() and i not in keep else c for i, c in enumerate(value))


def mask_value(value: str, kind: str | None = None) -> str:
    """Mask one value of a PII column according to its kind."""
    if kind == "email" or "@" in value:
        return mask_email(value)
    if any(c.isdigit() for c in value):
        return mask_digits(value)
    return f"{value[:1]}***" if value else value


def mask_text(value: str) -> str:
    """Mask emails, phone-like numbers and long digit runs inside free text."""
    value = _EMAIL_IN_TEXT.sub(lambda m: mask_email(m.group(0)), value)
    value = _PHONE_IN_TEXT.sub(lambda m: mask_digits(m.group(0)), value)
    return _LONG_DIGITS.sub(lambda m: mask_digits(m.group(0)), value)


def safe_sample(value: object) -> str:
    """Return a value that is safe to leave the profiler: masked and truncated."""
    text = mask_text(str(value).strip())
    return text[:MAX_SAMPLE_CHARS]
