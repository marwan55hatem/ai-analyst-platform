"""Locale-aware number formatting for insight text."""

_SEPARATORS = {
    "en": (",", "."),
    "de": (".", ","),
    "fr": (" ", ","),
    "ar": ("٬", "٫"),
}
_ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
LOCALES = tuple(_SEPARATORS)


def _group(value: float, decimals: int, locale: str) -> str:
    group, decimal = _SEPARATORS.get(locale, _SEPARATORS["en"])
    text = f"{abs(value):,.{decimals}f}"
    text = text.replace(",", "\x00").replace(".", decimal).replace("\x00", group)
    if locale == "ar":
        text = text.translate(_ARABIC_DIGITS)
    negative = value < 0 and float(f"{abs(value):.{decimals}f}") != 0
    return f"-{text}" if negative else text


def fmt_number(value: float, locale: str = "en", decimals: int = 0) -> str:
    """Format a plain number with the locale's separators."""
    return _group(value, decimals, locale)


def fmt_money(value: float, currency: str | None = None, locale: str = "en") -> str:
    """Format an amount with two decimals and the currency code (before, or after for Arabic)."""
    text = _group(value, 2, locale)
    if not currency:
        return text
    return f"{text} {currency}" if locale == "ar" else f"{currency} {text}"


def fmt_pct(value: float, locale: str = "en", decimals: int = 1) -> str:
    """Format a percentage (the input is already in percent, e.g. 12.5)."""
    return f"{_group(value, decimals, locale)}%"
