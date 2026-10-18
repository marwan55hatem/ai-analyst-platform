"""Plain-language wording for forecast insights.

Rules (tested): every title and summary says the figure is an estimate and puts the 80% range
next to the point value. When confidence is low, or the model was no better than the seasonal
naive baseline, the first sentence of the summary says the forecast is unreliable and why.
"""

from collections.abc import Callable

from app.ml.forecasting.engine import ForecastResult
from app.ml.forecasting.series import FREQS, period_label
from app.ml.format import fmt_money, fmt_number, fmt_pct

_NEXT = {"day": "tomorrow", "week": "next week", "month": "next month", "quarter": "next quarter"}
_MODEL_WORDS = {
    "SeasonalNaive": "seasonal naive (repeat the same period of the last cycle)",
    "Naive": "naive (repeat the last value)",
    "AutoETS": "exponential smoothing (ETS)",
    "AutoTheta": "Theta",
    "AutoARIMA": "ARIMA",
    "LightGBM": "LightGBM (gradient boosting on lags and calendar features)",
    "Constant": "constant",
}


def formatter(metric: str, currency: str | None, locale: str) -> Callable[[float], str]:
    """Return the number formatter for a metric (money for revenue and cost, else a count)."""
    if metric in ("revenue", "cost"):
        return lambda v: fmt_money(v, currency, locale)
    return lambda v: fmt_number(v, locale, 0)


def metric_label(metric: str) -> str:
    """Return the sentence-start label of a metric."""
    return {"revenue": "Net revenue", "cost": "Cost", "quantity": "Quantity sold"}.get(
        metric, metric.capitalize()
    )


def unreliable_reason(result: ForecastResult, confidence: str) -> str | None:
    """Return the plain-words reason the forecast is unreliable, or None when it is not."""
    if result.constant:
        return "every period had the same value, so no pattern could be learned or tested"
    if result.no_better_than_baseline:
        return (
            "it was no better than a seasonal naive baseline (simply repeating the same "
            "period of the previous cycle) when tested on past data"
        )
    if confidence == "low":
        reasons = result.confidence_reasons or ["the data does not support a firmer forecast"]
        return "; ".join(reasons)
    return None


def forecast_text(
    result: ForecastResult,
    *,
    metric: str,
    confidence: str,
    currency: str | None,
    locale: str,
    group: str | None = None,
) -> tuple[str, str]:
    """Return (title, summary) for one forecast, using the final (possibly capped) confidence."""
    fmt = formatter(metric, currency, locale)
    freq = FREQS[result.freq_key]
    label = metric_label(metric)
    subject = f"{label} for {group}" if group else label
    when = _NEXT[result.freq_key]
    point, lo, hi = result.point[0], result.lo80[0], result.hi80[0]
    title = (
        f"{subject} is estimated at about {fmt(point)} {when} (80% range: {fmt(lo)} to {fmt(hi)})"
    )
    parts: list[str] = []
    reason = unreliable_reason(result, confidence)
    if reason:
        parts.append(f"This forecast is unreliable: {reason}.")
    parts.append(
        f"It is an estimate, not a promise: {subject.lower() if not group else subject} is "
        f"estimated at about {fmt(point)} for {period_label(result.periods[0], freq)} "
        f"(80% range: {fmt(lo)} to {fmt(hi)}; 95% range: {fmt(result.lo95[0])} to "
        f"{fmt(result.hi95[0])})."
    )
    if len(result.point) > 1:
        total = sum(result.point)
        parts.append(
            f"Over the next {len(result.point)} {freq.plural} the estimated total is about "
            f"{fmt(total)}."
        )
    parts.append(_model_sentence(result, fmt, freq.plural, locale))
    if confidence == "medium":
        parts.append("Treat it as a guide rather than a firm number.")
    return title, " ".join(parts)


def _model_sentence(
    result: ForecastResult, fmt: Callable[[float], str], plural: str, locale: str
) -> str:
    words = _MODEL_WORDS.get(result.model, result.model)
    win, base = result.winner_score, result.baseline_score
    if win is None:
        return f"Method: {words}; it could not be tested on past data."
    text = (
        f"Method: {words}, chosen by testing on past data ({win.folds} rolling windows of "
        f"{result.backtest_horizon} {plural}). In those tests its estimates were off by about "
        f"{fmt_pct(win.smape, locale)} on average (sMAPE) and by about {fmt(win.mae)} per period "
        f"(MAE); its error was {fmt_number(win.mase, locale, 2)} times that of repeating the "
        "previous cycle (MASE, below 1 is better)."
    )
    if base is not None and base.name != win.name:
        text += (
            f" The seasonal naive baseline was off by about {fmt_pct(base.smape, locale)}"
            f" (MASE {fmt_number(base.mase, locale, 2)})."
        )
    return text
