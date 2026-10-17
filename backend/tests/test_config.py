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
