"""Render report facts to HTML (Jinja2, autoescaped) and to a tagged PDF (WeasyPrint).

Rendering has no network access: the only URLs the page holds are the bundled fonts, and the URL
fetcher refuses everything else (http, https, data, ftp and any other file) with a fatal error, so
no dataset text can make the renderer fetch anything (SSRF) or read a local file.
"""

import json
import urllib.parse
import urllib.request
from functools import cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from app.reports import charts
from app.reports.facts import ReportFacts, SegmentSection, TrendSection

HERE = Path(__file__).resolve().parent
FONTS_DIR = HERE / "fonts"
TEMPLATES_DIR = HERE / "templates"
I18N_DIR = HERE / "i18n"
FONTS = (
    ("IBM Plex Sans", "IBMPlexSans-Regular.ttf", 400),
    ("IBM Plex Sans", "IBMPlexSans-SemiBold.ttf", 600),
    ("IBM Plex Sans Arabic", "IBMPlexSansArabic-Regular.ttf", 400),
    ("IBM Plex Sans Arabic", "IBMPlexSansArabic-SemiBold.ttf", 600),
)
FAMILIES = {
    "en": '"IBM Plex Sans", "IBM Plex Sans Arabic"',
    "ar": '"IBM Plex Sans Arabic", "IBM Plex Sans"',
}


class ReportTooLarge(Exception):
    """The PDF is bigger than REPORT_MAX_MB."""


class BlockedURL(Exception):
    """Raised (as a fatal fetch error) when the page asks for anything but a bundled font."""


@cache
def messages(locale: str) -> dict[str, Any]:
    """Return the report strings of a locale."""
    data: dict[str, Any] = json.loads((I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))
    return data


def _font_path(url: str) -> Path | None:
    """Return the bundled font a file:// URL points to, or None for any other URL."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    path = Path(urllib.request.url2pathname(parsed.path)).resolve()
    if path.parent != FONTS_DIR or path.suffix != ".ttf" or not path.is_file():
        return None
    return path


def fonts_only_fetcher() -> Any:
    """Return a WeasyPrint URL fetcher that serves the bundled fonts and refuses the rest."""
    from weasyprint.urls import FatalURLFetchingError, URLFetcher, URLFetcherResponse

    class FontsOnlyFetcher(URLFetcher):  # type: ignore[misc]
        """Serve bundled fonts from disk; any other URL stops the rendering."""

        def fetch(self, url: str, headers: dict[str, str] | None = None) -> Any:
            path = _font_path(url)
            if path is None:
                raise FatalURLFetchingError(f"Blocked URL in a report: {url[:80]}")
            return URLFetcherResponse(url, path.read_bytes(), {"Content-Type": "font/ttf"})

    return FontsOnlyFetcher(timeout=1, allowed_protocols=("file",), fail_on_errors=True)


# ---- formatting -----------------------------------------------------------------------------


def _ltr(text: str) -> Markup:
    """Keep numbers, signs and dates left-to-right inside Arabic text."""
    return Markup('<span class="ltr">{}</span>').format(text)


def format_number(value: float | int | None, digits: int | None = None) -> str:
    """Return a number with thousands separators (Latin digits in both locales, like the UI)."""
    if value is None:
        return "–"
    if digits is None:
        digits = 0 if float(value).is_integer() or abs(float(value)) >= 100 else 2
    return f"{float(value):,.{digits}f}"


class Formatter:
    """Locale-aware formatting helpers handed to the template."""

    def __init__(self, facts: ReportFacts) -> None:
        """Bind the formatters to one report's locale and currency."""
        self.locale = facts.meta.locale
        self.currency = facts.meta.currency
        self.text = messages(self.locale)

    def t(self, key: str, **values: Any) -> str:
        """Return a translated string with its {placeholders} filled in."""
        template = str(self.text[key])
        if not values:
            return template
        wrap = self.locale == "ar"
        return template.format(
            **{k: isolate(_plain(v)) if wrap else _plain(v) for k, v in values.items()}
        )

    def iso(self, text: str) -> str:
        """Keep a date or number left-to-right inside an Arabic sentence (plain-text contexts)."""
        return isolate(text) if self.locale == "ar" else text

    def css(self, key: str) -> str:
        """Return a translated string escaped for a CSS string literal."""
        return self.t(key).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")

    def n(self, value: float | int | None, digits: int | None = None) -> Markup:
        """Format a plain number."""
        return _ltr(format_number(value, digits))

    def nfmt(self, value: float | int | None) -> str:
        """Format a number as plain text (for use inside translated sentences)."""
        return format_number(value)

    def money(self, value: float | None) -> Markup:
        """Format an amount, with the currency code when the profile states one."""
        if value is None:
            return _ltr("–")
        text = format_number(value)
        return _ltr(f"{self.currency} {text}" if self.currency else text)

    def pct(self, value: float | None) -> Markup:
        """Format a percentage."""
        return _ltr("–" if value is None else f"{value:.1f}%")

    def ch(self, value: float, unit: str) -> Markup:
        """Format a change: +12.3% or -1.5 pts."""
        value = round(value, 1) + 0.0  # no "-0.0"
        sign = "+" if value > 0 else ""
        if unit == "points":
            return _ltr(f"{sign}{value:.1f} {self.text['points']}")
        return _ltr(f"{sign}{value:.1f}%")

    def kv(self, value: float, unit: str) -> Markup:
        """Format a KPI value by its unit."""
        if unit == "money":
            return self.money(value)
        if unit == "percent":
            return self.pct(value)
        return self.n(value)

    def rng(self, low: float, high: float) -> Markup:
        """Format a range."""
        return _ltr(f"{format_number(low)} – {format_number(high)}")

    def d(self, value: str | None) -> Markup:
        """Format an ISO date as returned (YYYY-MM-DD)."""
        return _ltr(str(value)[:10] if value else "–")

    def p(self, period: str, frequency: str) -> Markup:
        """Format a period start: a month name for monthly data, the date otherwise."""
        if frequency == "month":
            year, month = int(period[:4]), int(period[5:7])
            return Markup("{} {}").format(self.text["months"][month - 1], _ltr(str(year)))
        return self.d(period)


def isolate(text: str) -> str:
    """Wrap text in a left-to-right embedding (LRE ... PDF) so bidi keeps its order."""
    return f"\u202a{text}\u202c"


def _plain(value: Any) -> str:
    if isinstance(value, Markup):
        return str(value.striptags())
    if isinstance(value, float):
        return format_number(value)
    return str(value)


@cache
def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _charts(facts: ReportFacts, fmt: Formatter) -> dict[str, Any]:
    trend = facts.trend
    segments = facts.segments
    rtl = facts.meta.locale == "ar"
    contributors = facts.contributors if isinstance(facts.contributors, list) else []
    return {
        "trend": charts.trend_svg(trend, fmt.t("trend")) if isinstance(trend, TrendSection) else "",
        "contributors": [
            charts.contributors_svg(table, fmt.t(f"dim_{table.dimension}"), rtl)
            for table in contributors
        ],
        "segments": (
            charts.segments_svg(segments, fmt.t("segments"), rtl)
            if isinstance(segments, SegmentSection)
            else ""
        ),
    }


def render_html(facts: ReportFacts) -> str:
    """Return the report as one self-contained HTML document."""
    fmt = Formatter(facts)
    rtl = facts.meta.locale == "ar"
    fonts = [
        {"family": family, "uri": (FONTS_DIR / name).as_uri(), "weight": weight}
        for family, name, weight in FONTS
    ]
    return (
        _environment()
        .get_template("report.html.j2")
        .render(
            facts=facts,
            charts=_charts(facts, fmt),
            fonts=fonts,
            dir="rtl" if rtl else "ltr",
            start="right" if rtl else "left",
            end="left" if rtl else "right",
            family=Markup(FAMILIES[facts.meta.locale]),
            t=fmt.t,
            css=fmt.css,
            iso=fmt.iso,
            n=fmt.n,
            nfmt=fmt.nfmt,
            money=fmt.money,
            pct=fmt.pct,
            ch=fmt.ch,
            kv=fmt.kv,
            rng=fmt.rng,
            d=fmt.d,
            p=fmt.p,
        )
    )


def render_pdf(facts: ReportFacts, max_bytes: int) -> bytes:
    """Return the report as a tagged PDF (PDF/UA-1), refusing results above `max_bytes`."""
    from weasyprint import HTML
    from weasyprint.text.fonts import FontConfiguration

    html = render_html(facts)
    pdf: bytes = HTML(string=html, base_url=None, url_fetcher=fonts_only_fetcher()).write_pdf(
        font_config=FontConfiguration(), pdf_variant="pdf/ua-1"
    )
    if len(pdf) > max_bytes:
        raise ReportTooLarge(f"The report is {len(pdf)} bytes; the limit is {max_bytes}.")
    return pdf
