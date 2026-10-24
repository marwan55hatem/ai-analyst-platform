"""Picks the model for a request: the cheap main model unless the question needs harder analysis."""

import re
from typing import Literal

from app.core.config import Settings

Purpose = Literal["answer", "summary"]

# Decision, causal and scenario questions go to the strong model (English and Arabic).
_HARD = re.compile(
    r"\b(what should|should i|should we|recommend|why|what if|what-if|scenario|strategy|"
    r"decide|decision|prioriti[sz]e|grow profit|improve)\b"
    r"|ماذا يجب|هل يجب|أنصح|توصي|لماذا|ماذا لو|سيناريو|استراتيجية|قرار",
    re.IGNORECASE,
)


def choose_model(question: str, settings: Settings, purpose: Purpose = "answer") -> str:
    """Return LLM_MODEL_STRONG for decision/why/what-if questions, else LLM_MODEL."""
    main = settings.llm_model or "deepseek-flash"
    strong = settings.llm_model_strong or main
    if purpose == "summary":
        return main
    return strong if _HARD.search(question) else main
