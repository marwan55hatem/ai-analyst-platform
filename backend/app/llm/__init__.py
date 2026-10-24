"""LLM access behind the provider-neutral `LLMClient` interface."""

import threading

import structlog

from app.core.config import Settings, get_settings
from app.llm.base import LLMClient, LLMError, LLMNotConfiguredError
from app.llm.fake import FakeLLMClient
from app.llm.scripted import ScriptedAnalyst

__all__ = [
    "FakeLLMClient",
    "LLMClient",
    "LLMError",
    "ScriptedAnalyst",
    "ensure_provider_allowed",
    "get_llm_client",
    "is_scripted",
]

REAL_PROVIDERS = {"deepseek", "openai_compatible"}
logger = structlog.get_logger()
_lock = threading.Lock()
_real: dict[tuple[str, ...], LLMClient] = {}


def ensure_provider_allowed(settings: Settings) -> None:
    """Refuse the fake provider in production; warn loudly whenever it is used."""
    provider = settings.llm_provider.lower()
    if provider != "fake":
        return
    if settings.app_env == "production":
        raise RuntimeError(
            "LLM_PROVIDER=fake is a scripted test model and cannot run with APP_ENV=production."
        )
    logger.warning(
        "llm_provider_fake",
        message="LLM_PROVIDER=fake: answers come from the SCRIPTED TEST MODEL, not a real LLM",
    )


def is_scripted(settings: Settings | None = None) -> bool:
    """Return whether answers come from the scripted test model."""
    return (settings or get_settings()).llm_provider.lower() == "fake"


def get_llm_client() -> LLMClient | None:
    """Return the configured client, or None when no provider is set (LLM steps are skipped)."""
    settings = get_settings()
    provider = settings.llm_provider.lower()
    if provider in {"", "none"}:
        return None
    if provider == "fake":
        if settings.app_env == "production":
            raise LLMNotConfiguredError("LLM_PROVIDER=fake is not allowed in production.")
        return ScriptedAnalyst()
    if provider in REAL_PROVIDERS:
        return _real_client(settings)
    raise LLMError(f"Unknown LLM_PROVIDER '{provider}' (use deepseek, fake or none).")


def _real_client(settings: Settings) -> LLMClient:
    from app.llm.openai_compatible import OpenAICompatibleClient

    key = (
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_thinking,
        str(settings.llm_timeout_s),
        str(settings.llm_max_retries),
        str(settings.llm_max_output_tokens),
        settings.llm_model,
    )
    with _lock:
        client = _real.get(key)
        if client is None:
            client = OpenAICompatibleClient(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                thinking=settings.llm_thinking == "enabled",
                timeout_s=settings.llm_timeout_s,
                max_retries=settings.llm_max_retries,
                max_output_tokens=settings.llm_max_output_tokens,
                json_model=settings.llm_model or "deepseek-flash",
            )
            _real[key] = client
    return client
