"""The provider-neutral LLM interface; no vendor SDK is imported outside this package."""

from typing import Any, Protocol


class LLMError(Exception):
    """The model could not be reached or returned an unusable answer."""


class LLMClient(Protocol):
    """Minimal interface every provider implements (real providers arrive in Step 8)."""

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Send a prompt and return the model's answer parsed as a JSON object."""
        ...
