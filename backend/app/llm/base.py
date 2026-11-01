"""The provider-neutral LLM interface and typed errors; no vendor SDK is imported outside here."""

import math
from collections.abc import Iterator
from typing import Any, Protocol

from app.llm.types import ChatMessage, LLMEvent, ToolSpec

LONG_WAIT_S = 120


class LLMError(Exception):
    """The model could not be reached or returned an unusable answer.

    `code` is stable and shown to clients; `user_message` is safe to show (never contains keys).
    """

    code = "provider_error"
    default_message = "The model provider returned an error."
    retryable = False

    def __init__(
        self, message: str | None = None, *, code: str | None = None, status: int | None = None
    ) -> None:
        """Create the error; `message` overrides the class's user message."""
        self.user_message = message or self.default_message
        super().__init__(self.user_message)
        self.code = code or self.code
        self.status = status


class LLMAuthError(LLMError):
    """401: the API key is missing or wrong."""

    code = "provider_auth_failed"
    default_message = "The model provider rejected the API key. Check LLM_API_KEY."


class LLMNoBalanceError(LLMError):
    """402: the provider account has no balance."""

    code = "provider_no_balance"
    default_message = (
        "The model provider has no balance. Top up the provider account; no answer was generated."
    )


class LLMRateLimitError(LLMError):
    """429: too many requests or too much concurrency at the provider."""

    code = "provider_rate_limited"
    default_message = "The model provider is rate limiting requests. Try again in a minute."
    retryable = True

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status: int | None = None,
        retry_after_s: int | None = None,
    ) -> None:
        """Create the error; `retry_after_s` puts the provider's wait into the user message."""
        super().__init__(message or _rate_limit_message(retry_after_s), code=code, status=status)
        self.retry_after_s = retry_after_s


def _rate_limit_message(retry_after_s: int | None) -> str | None:
    if retry_after_s is None:
        return None
    if retry_after_s <= LONG_WAIT_S:
        wait = f"{retry_after_s} seconds"
    else:
        wait = f"about {math.ceil(retry_after_s / 60)} minutes"
    return f"The model provider is rate limiting requests. Try again in {wait}."


class LLMUnavailableError(LLMError):
    """5xx or connection failure: the provider is down or overloaded."""

    code = "provider_unavailable"
    default_message = "The model provider is unavailable right now. Try again later."
    retryable = True


class LLMTimeoutError(LLMError):
    """The provider did not answer in time."""

    code = "provider_timeout"
    default_message = "The model provider did not answer in time. Try again."


class LLMBadRequestError(LLMError):
    """400/422: the request was rejected as invalid (a bug on our side, never retried)."""

    code = "provider_bad_request"
    default_message = "The model provider rejected the request."


class LLMRequestTooLargeError(LLMError):
    """413 or context_length_exceeded: the prompt does not fit the provider's limit."""

    code = "provider_request_too_large"
    default_message = (
        "This question needs more context than the model allows. "
        "Ask something narrower or start a new conversation."
    )


class LLMModelNotFoundError(LLMError):
    """404 or a retired model: the configured model does not exist at the provider."""

    code = "provider_model_not_found"
    default_message = "The configured model is not available at the provider. Check LLM_MODEL."


class LLMToolCallError(LLMError):
    """The model produced a malformed tool call or called a tool that does not exist."""

    code = "provider_bad_tool_call"
    default_message = "The model produced an invalid tool call twice. Try rephrasing the question."


class LLMNotConfiguredError(LLMError):
    """No provider is configured (LLM_PROVIDER=none)."""

    code = "llm_not_configured"
    default_message = "No model provider is configured (LLM_PROVIDER=none)."


class LLMClient(Protocol):
    """Interface every provider implements."""

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Send a prompt and return the model's answer parsed as a JSON object."""
        ...

    def chat(
        self, messages: list[ChatMessage], tools: list[ToolSpec], *, model: str
    ) -> Iterator[LLMEvent]:
        """Stream one model turn: zero or more `TextDelta`, then exactly one `Completion`."""
        ...
