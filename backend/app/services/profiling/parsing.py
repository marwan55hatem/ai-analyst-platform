"""SQL expression builders that turn messy text into numbers, booleans and dates.

The profiler (to measure) and the cleaning step (to fix) both use these builders, so the two
always agree on what is parseable. Every expression is built from a quoted column identifier
that comes from the Parquet schema, never from user input.
"""

import re

# Arabic-Indic and Persian digits, Arabic decimal (٫), thousands (٬) and comma (،), percent (٪).
_AR_FROM = "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬،٪"
_AR_TO = "01234567890123456789.,,%"
_CURRENCY = r"(?i)(usd|eur|gbp|egp|sar|aed|kwd|جنيه|ج\.م|ريال|ر\.س|دولار|درهم|\p{Sc})"
_NUMBER_SHAPE = r"^[-+]?([0-9][0-9.,]*|[.][0-9]+)%?$"
_THOUSANDS_COMMA = r"^[-+]?[0-9]{1,3}(,[0-9]{3})+$"
_THOUSANDS_DOT = r"^[-+]?[0-9]{1,3}([.][0-9]{3}){2,}$"

TRUE_WORDS = ("true", "t", "yes", "y", "نعم", "صح")
FALSE_WORDS = ("false", "f", "no", "n", "لا", "خطأ")

ISO_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
)
TEXT_FORMATS = ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d-%b-%y")
DMY_FORMATS = ("%d/%m/%Y", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%y")
MDY_FORMATS = ("%m/%d/%Y", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%y")

_SLASH_FAMILY = r"^(\d{1,2})[-./](\d{1,2})[-./](\d{2,4})"


def quote_ident(name: str) -> str:
    """Quote a column name for DuckDB."""
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    """Quote a string literal for DuckDB."""
    return "'" + value.replace("'", "''") + "'"


def present_sql(ident: str, dtype: str) -> str:
    """Return a predicate that is true for non-null values (and non-blank text)."""
    if dtype.startswith("VARCHAR"):
        return f"({ident} IS NOT NULL AND trim({ident}) <> '')"
    return f"{ident} IS NOT NULL"


def text_of(ident: str) -> str:
    """Return the column as trimmed text with Arabic digits translated to ASCII digits."""
    trimmed = f"trim(CAST({ident} AS VARCHAR))"
    return f"translate({trimmed}, {quote_literal(_AR_FROM)}, {quote_literal(_AR_TO)})"


def clean_number_text(ident: str) -> str:
    """Return text with currency, spaces and accounting parentheses removed."""
    s = text_of(ident)
    s = f"regexp_replace({s}, '[\\s\\x{{00a0}}]+', '', 'g')"
    s = f"regexp_replace({s}, {quote_literal(_CURRENCY)}, '', 'g')"
    return f"regexp_replace({s}, '^[(](.*)[)]$', '-\\1')"


def number_from_clean(s: str) -> str:
    """Parse text already cleaned by `clean_number_text`; NULL when it is not a number.

    Handles thousands separators (1,234.50 and 1.234,50), decimal commas (12,5) and a trailing
    percent sign (the result is then a fraction: 15% becomes 0.15).
    """
    body = f"rtrim({s}, '%')"
    last_comma = f"strpos(reverse({body}), ',')"
    last_dot = f"strpos(reverse({body}), '.')"
    european = f"({last_comma} > 0 AND {last_comma} < {last_dot})"
    both = f"(strpos({body}, ',') > 0 AND strpos({body}, '.') > 0)"
    normalized = (
        f"CASE "
        f"WHEN {both} AND {european} THEN replace(replace({body}, '.', ''), ',', '.') "
        f"WHEN {both} THEN replace({body}, ',', '') "
        f"WHEN strpos({body}, ',') > 0 "
        f"AND regexp_matches({body}, {quote_literal(_THOUSANDS_COMMA)}) "
        f"THEN replace({body}, ',', '') "
        f"WHEN strpos({body}, ',') > 0 THEN replace({body}, ',', '.') "
        f"WHEN regexp_matches({body}, {quote_literal(_THOUSANDS_DOT)}) "
        f"THEN replace({body}, '.', '') "
        f"ELSE {body} END"
    )
    value = f"TRY_CAST({normalized} AS DOUBLE)"
    scale = f"CASE WHEN {s} LIKE '%\\%' ESCAPE '\\' THEN 100.0 ELSE 1.0 END"
    return (
        f"CASE WHEN regexp_matches({s}, {quote_literal(_NUMBER_SHAPE)}) THEN {value} / {scale} END"
    )


def number_sql(ident: str) -> str:
    """Return a DOUBLE expression for a text column holding messy numbers (NULL if unparseable)."""
    return number_from_clean(clean_number_text(ident))


def bool_sql(ident: str) -> str:
    """Return a BOOLEAN expression for yes/no/true/false text (NULL if unrecognised)."""
    s = f"lower(trim(CAST({ident} AS VARCHAR)))"
    true_list = ", ".join(quote_literal(w) for w in TRUE_WORDS)
    false_list = ", ".join(quote_literal(w) for w in FALSE_WORDS)
    return f"CASE WHEN {s} IN ({true_list}) THEN TRUE WHEN {s} IN ({false_list}) THEN FALSE END"


def date_sql(ident: str, *, dayfirst: bool = True) -> str:
    """Return a TIMESTAMP expression for text dates in many formats (NULL if unparseable).

    For values like 03/04/2024 that fit both orders, `dayfirst` decides; a value such as
    25/04/2024 or 04/25/2024 can only be one order and is always read correctly.
    """
    x = text_of(ident)
    x2 = f"regexp_replace({x}, '^(\\d{{1,2}})[-.](\\d{{1,2}})[-.](\\d{{2,4}})', '\\1/\\2/\\3')"
    first, second = (DMY_FORMATS, MDY_FORMATS) if dayfirst else (MDY_FORMATS, DMY_FORMATS)
    parts = [f"try_strptime({x}, {quote_literal(f)})" for f in ISO_FORMATS]
    parts += [f"try_strptime({x2}, {quote_literal(f)})" for f in (*first, *second)]
    parts += [f"try_strptime({x}, {quote_literal(f)})" for f in TEXT_FORMATS]
    return "COALESCE(" + ", ".join(parts) + ")"


def slash_family_parts(ident: str) -> tuple[str, str, str]:
    """Return expressions for the first, second and third number of `d/m/Y`-like text."""
    x = text_of(ident)
    pattern = quote_literal(_SLASH_FAMILY)
    return (
        f"TRY_CAST(NULLIF(regexp_extract({x}, {pattern}, 1), '') AS INTEGER)",
        f"TRY_CAST(NULLIF(regexp_extract({x}, {pattern}, 2), '') AS INTEGER)",
        f"TRY_CAST(NULLIF(regexp_extract({x}, {pattern}, 3), '') AS INTEGER)",
    )


_ARABIC_DIGITS = str.maketrans(_AR_FROM, _AR_TO)


def normalize_digits(text: str) -> str:
    """Python twin of the SQL digit translation (used in tests and for samples)."""
    return text.translate(_ARABIC_DIGITS)


def looks_like_number(text: str) -> bool:
    """Return True for text that is a plain number after the same cleanup as the SQL (cheap)."""
    return bool(re.match(_NUMBER_SHAPE, normalize_digits(text).replace(" ", "")))
