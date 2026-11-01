"""System prompt (versioned file) plus the company profile and conversation summary."""

from functools import lru_cache
from pathlib import Path

from app.agent.untrusted import neutralise
from app.models.agent import BusinessProfile
from app.models.dataset import Dataset

PROMPT_VERSION = "system_v2"
_PROMPTS = Path(__file__).with_name("prompts")
LANGUAGES = {"en": "English", "ar": "Arabic"}


@lru_cache
def load_prompt(version: str = PROMPT_VERSION) -> str:
    """Return the text of a versioned system prompt."""
    return (_PROMPTS / f"{version}.md").read_text(encoding="utf-8")


def build_system_prompt(
    profile: BusinessProfile | None, dataset: Dataset, summary: str = ""
) -> str:
    """Combine the rules, the company profile, the dataset name and the conversation summary."""
    parts = [load_prompt()]
    lines = [f"- Dataset: {neutralise(dataset.name)} (table `data`)"]
    if profile is not None:
        fields = (
            ("Industry", profile.industry),
            ("Goals", profile.goals),
            ("Currency", profile.currency),
            ("Preferred language", LANGUAGES.get(profile.language, profile.language)),
            ("Constraints", profile.constraints),
        )
        lines += [f"- {label}: {neutralise(value)}" for label, value in fields if value]
        lines.append(
            "- Personal data in answers: "
            + ("allowed by the company" if profile.allow_pii else "not allowed")
        )
    parts.append(
        "Company profile (set by the company, not by the user's question):\n" + "\n".join(lines)
    )
    if summary:
        parts.append(
            "Summary of the earlier conversation (its numbers are not evidence for this turn):\n"
            + neutralise(summary)
        )
    return "\n\n".join(parts)
