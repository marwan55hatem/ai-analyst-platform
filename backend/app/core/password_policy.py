"""Password policy: length, common passwords, and personal information."""

from app.core.common_passwords import COMMON_PASSWORDS
from app.core.errors import UnprocessableError

MIN_LENGTH = 10
MAX_LENGTH = 128


def password_problems(
    password: str, *, email: str | None = None, full_name: str | None = None
) -> list[str]:
    """Return human-readable policy violations (empty when the password is acceptable)."""
    problems: list[str] = []
    lowered = password.lower()
    if len(password) < MIN_LENGTH:
        problems.append(f"must be at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        problems.append(f"must be at most {MAX_LENGTH} characters")
    if lowered in COMMON_PASSWORDS:
        problems.append("is too common")
    if len(set(password)) < 4:
        problems.append("is too repetitive")
    local_part = email.split("@")[0].lower() if email else ""
    if len(local_part) >= 4 and local_part in lowered:
        problems.append("must not contain your email name")
    if full_name and len(full_name.replace(" ", "")) >= 4 and full_name.lower() in lowered:
        problems.append("must not contain your name")
    return problems


def validate_password(
    password: str, *, email: str | None = None, full_name: str | None = None
) -> None:
    """Raise a 422 `weak_password` error when the password breaks the policy."""
    problems = password_problems(password, email=email, full_name=full_name)
    if problems:
        raise UnprocessableError(
            "Password does not meet the policy", code="weak_password", details=problems
        )
