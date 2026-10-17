"""Application settings loaded from environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; every field maps to a variable in .env.example."""

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_name: str = "AI Business Analyst Platform"
    database_url: str = "postgresql+psycopg://analyst:analyst@localhost:5432/analyst"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 14
    llm_provider: str = "none"
    llm_api_key: str = ""
    llm_model: str = ""
    storage_dir: str = "./storage"
    max_upload_mb: int = 50
    max_rows: int = 2_000_000
    query_timeout_s: float = 30.0
    ingest_stuck_minutes: int = 30
    query_memory_mb: int = 512
    query_threads: int = 2
    mlflow_tracking_uri: str = "http://localhost:5000"
    cors_origins: str = Field(default="http://localhost:3000")
    email_provider: str = "none"
    email_api_key: str = ""
    log_level: str = "INFO"
    cookie_secure: bool = True
    rate_limit_enabled: bool = True

    def ensure_production_safe(self) -> None:
        """Refuse to run with the placeholder or a short JWT secret."""
        if self.jwt_secret == "change-me" or len(self.jwt_secret) < 32:
            raise ValueError(
                "JWT_SECRET must be set to a random value of at least 32 characters (see .env)"
            )

    @property
    def cors_origin_list(self) -> list[str]:
        """Return CORS_ORIGINS as a list of non-empty origins."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
