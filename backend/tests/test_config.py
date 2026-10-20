from app.core.config import Settings


def test_settings_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MAX_UPLOAD_MB", "7")
    assert Settings().max_upload_mb == 7


def test_settings_defaults() -> None:
    assert Settings().jwt_algorithm == "HS256"


def test_placeholder_or_short_jwt_secret_is_refused() -> None:
    import pytest

    from app.core.config import Settings

    for bad in ("change-me", "too-short"):
        with pytest.raises(ValueError):
            Settings(jwt_secret=bad).ensure_production_safe()
    Settings(jwt_secret="x" * 32).ensure_production_safe()


def test_pseudonym_key_is_separate_or_derived_with_a_startup_warning() -> None:
    from pathlib import Path

    from structlog.testing import capture_logs

    from app.core.config import warn_derived_pseudonym_secret

    own = Settings(pseudonym_secret="p" * 32, jwt_secret="j" * 32)
    derived = Settings(pseudonym_secret="", jwt_secret="j" * 32)
    assert own.pseudonym_key == b"p" * 32
    assert derived.pseudonym_key != b"j" * 32 and derived.pseudonym_key.endswith(b"j" * 32)
    with capture_logs() as logs:
        assert warn_derived_pseudonym_secret(derived) is True
        assert warn_derived_pseudonym_secret(own) is False
    assert [entry["event"] for entry in logs] == ["pseudonym_secret_derived_from_jwt"]
    assert "PSEUDONYM_SECRET" in Path(__file__).resolve().parents[2].joinpath(
        ".env.example"
    ).read_text(encoding="utf-8")


def test_env_example_documents_every_setting() -> None:
    from pathlib import Path

    text = Path(__file__).resolve().parents[2].joinpath(".env.example").read_text(encoding="utf-8")
    documented = {
        line.split("=", 1)[0].strip().lower()
        for line in text.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    missing = [
        name for name in Settings.model_fields if name not in documented and name != "app_name"
    ]
    assert missing == []
