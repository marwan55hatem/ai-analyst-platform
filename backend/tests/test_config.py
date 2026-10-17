from app.core.config import Settings


def test_settings_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MAX_UPLOAD_MB", "7")
    assert Settings().max_upload_mb == 7


def test_settings_defaults() -> None:
    assert Settings().jwt_algorithm == "HS256"
