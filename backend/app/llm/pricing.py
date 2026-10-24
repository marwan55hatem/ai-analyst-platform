"""Approximate cost of a model call from per-million-token prices in settings."""

from app.core.config import Settings
from app.llm.types import Usage

PER_MILLION = 1_000_000


def approximate_cost_usd(usage: Usage, model: str, settings: Settings) -> float:
    """Return the approximate USD cost (strong-model prices when `model` is LLM_MODEL_STRONG).

    Cached prompt tokens are billed at the cached-input price, the rest at the input price;
    reasoning tokens are part of the completion tokens.
    """
    if model == settings.llm_model_strong:
        prices = (
            settings.llm_price_strong_input_per_m,
            settings.llm_price_strong_cached_input_per_m,
            settings.llm_price_strong_output_per_m,
        )
    else:
        prices = (
            settings.llm_price_input_per_m,
            settings.llm_price_cached_input_per_m,
            settings.llm_price_output_per_m,
        )
    cached = min(usage.cached_tokens, usage.prompt_tokens)
    fresh = usage.prompt_tokens - cached
    cost = fresh * prices[0] + cached * prices[1] + usage.completion_tokens * prices[2]
    return round(cost / PER_MILLION, 6)
