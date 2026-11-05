"""Inline SVG charts for reports, drawn from report facts by plain Python.

Only numbers and ISO periods are written inside the SVG. Names, legends and units live in the HTML
around the chart (so Arabic text is shaped by the page, and dataset text is escaped once by the
template), and every chart has its numbers in a table next to it. Colours are the light-theme chart
tokens of docs/design.md.
"""

from html import escape

from app.reports.facts import ContributorTable, SegmentSection, TrendSection

ACTUAL = "#0b5c63"
FORECAST = "#8a5a00"
BAND_80 = "rgba(184,110,0,0.55)"
BAND_95 = "rgba(184,110,0,0.22)"
GRID = "#d3dcda"
INK = "#4b5a58"
BAR = "#0b5c63"
BAR_SOFT = "#9ccbce"
# Outline that gives the light bars 3:1 contrast against white (WCAG 1.4.11)
BAR_EDGE = "#2a7a80"

WIDTH, HEIGHT = 640, 240
PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 56, 12, 12, 28


def compact(value: float) -> str:
    """Return a short axis label: 1.2M, 340k, 12."""
    size = abs(value)
    if size >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if size >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if size >= 1_000:
        return f"{value / 1_000:.0f}k"
    return f"{value:.0f}"


def _ticks(low: float, high: float, count: int = 4) -> list[float]:
    if high <= low:
        return [low]
    step = (high - low) / count
    return [low + step * i for i in range(count + 1)]


def _svg(body: str, label: str, height: int = HEIGHT) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" '
        f'width="100%" role="img" aria-label="{escape(label)}" class="chart" '
        f'font-family="IBM Plex Sans" font-size="11">{body}</svg>'
    )


def trend_svg(trend: TrendSection, label: str) -> str:
    """Return the history line, the forecast line and its 80% and 95% bands."""
    periods = [h.period for h in trend.history] + [f.period for f in trend.forecast]
    if len(periods) < 2:
        return ""
    values = [h.value for h in trend.history]
    for f in trend.forecast:
        values.extend((f.lo95, f.hi95, f.point))
    low, high = min(0.0, min(values)), max(values)
    if high == low:
        high = low + 1
    inner_w = WIDTH - PAD_LEFT - PAD_RIGHT
    inner_h = HEIGHT - PAD_TOP - PAD_BOTTOM

    def x(i: int) -> float:
        return PAD_LEFT + inner_w * i / (len(periods) - 1)

    def y(v: float) -> float:
        return PAD_TOP + inner_h * (1 - (v - low) / (high - low))

    parts = []
    for tick in _ticks(low, high):
        parts.append(
            f'<line x1="{PAD_LEFT}" x2="{WIDTH - PAD_RIGHT}" y1="{y(tick):.1f}" '
            f'y2="{y(tick):.1f}" stroke="{GRID}" stroke-width="0.6"/>'
            f'<text x="{PAD_LEFT - 6}" y="{y(tick) + 4:.1f}" text-anchor="end" '
            f'fill="{INK}">{compact(tick)}</text>'
        )
    first = len(trend.history) - 1 if trend.history else 0
    if trend.forecast:
        for lo, hi, colour in (("lo95", "hi95", BAND_95), ("lo80", "hi80", BAND_80)):
            top = [
                f"{x(first + 1 + i):.1f},{y(getattr(f, hi)):.1f}"
                for i, f in enumerate(trend.forecast)
            ]
            bottom = [
                f"{x(first + 1 + i):.1f},{y(getattr(f, lo)):.1f}"
                for i, f in enumerate(trend.forecast)
            ]
            if trend.history:
                anchor = f"{x(first):.1f},{y(trend.history[-1].value):.1f}"
                top.insert(0, anchor)
                bottom.insert(0, anchor)
            parts.append(f'<polygon points="{" ".join(top + bottom[::-1])}" fill="{colour}"/>')
        line = [f"{x(first + 1 + i):.1f},{y(f.point):.1f}" for i, f in enumerate(trend.forecast)]
        if trend.history:
            line.insert(0, f"{x(first):.1f},{y(trend.history[-1].value):.1f}")
        parts.append(
            f'<polyline points="{" ".join(line)}" fill="none" stroke="{FORECAST}" '
            f'stroke-width="2" stroke-dasharray="5 3"/>'
        )
    if trend.history:
        line = [f"{x(i):.1f},{y(h.value):.1f}" for i, h in enumerate(trend.history)]
        parts.append(
            f'<polyline points="{" ".join(line)}" fill="none" stroke="{ACTUAL}" stroke-width="2"/>'
        )
    for i in sorted({0, first, len(periods) - 1}):
        anchor = "start" if i == 0 else "end" if i == len(periods) - 1 else "middle"
        parts.append(
            f'<text x="{x(i):.1f}" y="{HEIGHT - 8}" text-anchor="{anchor}" '
            f'fill="{INK}">{escape(periods[i][:7])}</text>'
        )
    return _svg("".join(parts), label)


def bars_svg(values: list[float], label: str, highlight: int = 0, rtl: bool = False) -> str:
    """Return horizontal bars numbered 1..n (the names are in the table next to the chart).

    In a right-to-left report the bars grow from the right, matching the table beside them.
    """
    if not values:
        return ""
    row, gap = 18, 6
    height = PAD_TOP + len(values) * (row + gap) + 4
    top = max(max(values), 0.0) or 1.0
    inner = WIDTH - 40 - 70
    parts = []
    for i, value in enumerate(values):
        y = PAD_TOP + i * (row + gap)
        width = max(1.0, inner * max(value, 0.0) / top)
        colour = BAR if i < highlight else BAR_SOFT
        if rtl:
            rank_x, rank_anchor = WIDTH - 30, "start"
            bar_x = WIDTH - 40 - width
            value_x, value_anchor = bar_x - 6, "end"
        else:
            rank_x, rank_anchor = 30, "end"
            bar_x = 40.0
            value_x, value_anchor = 40 + width + 6, "start"
        parts.append(
            f'<text x="{rank_x}" y="{y + 13}" text-anchor="{rank_anchor}" '
            f'fill="{INK}">{i + 1}</text>'
            f'<rect x="{bar_x:.1f}" y="{y}" width="{width:.1f}" height="{row}" fill="{colour}" '
            f'stroke="{BAR_EDGE}" stroke-width="1"/>'
            f'<text x="{value_x:.1f}" y="{y + 13}" text-anchor="{value_anchor}" '
            f'fill="{INK}">{compact(value)}</text>'
        )
    return _svg("".join(parts), label, height)


def contributors_svg(table: ContributorTable, label: str, rtl: bool = False) -> str:
    """Return the bars of a Pareto table; A-class items are drawn darker."""
    highlight = sum(1 for item in table.items if item.abc == "A")
    return bars_svg([item.revenue for item in table.items], label, highlight, rtl)


def segments_svg(section: SegmentSection, label: str, rtl: bool = False) -> str:
    """Return the customers per segment."""
    return bars_svg([float(s.customers) for s in section.segments], label, 0, rtl)
