"""LLM access behind the provider-neutral `LLMClient` interface."""

from app.core.config import get_settings
from app.llm.base import LLMClient, LLMError
from app.llm.fake import FakeLLMClient

__all__ = ["FakeLLMClient", "LLMClient", "LLMError", "get_llm_client"]


def get_llm_client() -> LLMClient | None:
    """Return the configured client, or None when no provider is set (LLM steps are skipped).

    Real providers are added in Step 8; until then only `none` and `fake` are valid.
    """
    provider = get_settings().llm_provider.lower()
    if provider in {"", "none"}:
        return None
    if provider == "fake":
        return FakeLLMClient()
    raise LLMError(f"LLM provider '{provider}' is not available yet (real providers: Step 8).")
