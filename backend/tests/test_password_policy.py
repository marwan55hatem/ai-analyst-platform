import pytest

from app.core.errors import UnprocessableError
from app.core.password_policy import password_problems, validate_password


@pytest.mark.parametrize(
    "password",
    ["correct-horse-battery", "Tr0ub4dor&3-extra", "a1b2c3d4e5f6g7"],
)
def test_acceptable_passwords(password: str) -> None:
    assert password_problems(password) == []
    validate_password(password)


@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("short1", "at least 10"),
        ("Password123", "too common"),
        ("aaaaaaaaaaaa", "repetitive"),
        ("x" * 129, "at most 128"),
    ],
)
def test_rejected_passwords(password: str, fragment: str) -> None:
    assert any(fragment in p for p in password_problems(password))
    with pytest.raises(UnprocessableError) as excinfo:
        validate_password(password)
    assert excinfo.value.code == "weak_password"


def test_personal_information_is_rejected() -> None:
    assert password_problems("maria.garcia-2024!", email="maria.garcia@example.com")
    assert password_problems("hello olivia owner!", full_name="Olivia Owner")
    assert not password_problems("unrelated-passphrase", email="maria@example.com")
