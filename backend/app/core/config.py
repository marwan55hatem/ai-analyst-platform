"""Application settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal

import structlog
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
    app_env: Literal["development", "test", "production"] = "development"
    llm_provider: str = "none"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = Field(default="", repr=False)
    llm_model: str = "deepseek-flash"
    llm_model_strong: str = "deepseek-v4-pro"
    llm_thinking: Literal["enabled", "disabled"] = "enabled"
    llm_timeout_s: float = 120.0
    llm_max_retries: int = Field(default=3, ge=0, le=6)
    llm_max_output_tokens: int = Field(default=4096, ge=256, le=65_536)
    llm_price_input_per_m: float = 0.44
    llm_price_cached_input_per_m: float = 0.014
    llm_price_output_per_m: float = 1.32
    llm_price_strong_input_per_m: float = 1.32
    llm_price_strong_cached_input_per_m: float = 0.044
    llm_price_strong_output_per_m: float = 3.96
    agent_daily_token_budget: int = Field(default=200_000, ge=0)
    agent_max_iterations: int = Field(default=8, ge=1, le=20)
    agent_history_messages: int = Field(default=12, ge=2, le=100)
    agent_summary_after: int = Field(default=16, ge=4, le=500)
    agent_tool_output_chars: int = Field(default=12_000, ge=1_000, le=100_000)
    chat_rate_limit_per_minute: int = Field(default=20, ge=1)
    chat_max_concurrent_streams: int = Field(default=2, ge=1, le=20)
    sandbox_url: str = "unix:///ipc/sandbox.sock"
    sandbox_timeout_s: float = Field(default=15.0, gt=0, le=120)
    sandbox_max_upload_mb: float = Field(default=20.0, gt=0, le=200)
    storage_dir: str = "./storage"
    max_upload_mb: int = 50
    max_rows: int = 2_000_000
    query_timeout_s: float = 30.0
    ingest_stuck_minutes: int = 30
    query_memory_mb: int = 512
    query_threads: int = 2
    profile_memory_mb: int = 512
    analysis_memory_mb: int = 512
    auto_analyze: bool = True
    mlflow_tracking_uri: str = "http://localhost:5000"
    forecast_max_seconds: float = 60.0
    forecast_max_series: int = 11
    forecast_max_horizon: int = 24
    forecast_max_groups: int = 10
    forecast_memory_mb: int = 400
    forecast_seed: int = 42
    ml_seed: int = 42
    anomaly_row_sample: int = 50_000
    anomaly_max_seconds: float = 60.0
    segment_max_customers: int = 100_000
    segment_bootstrap_rounds: int = 20
    segment_max_seconds: float = 60.0
    churn_max_customers: int = 200_000
    churn_max_seconds: float = 60.0
    driver_max_rows: int = 50_000
    driver_shap_rows: int = 2_000
    driver_max_seconds: float = 60.0
    whatif_max_concurrent: int = 2
    timeseries_max_points: int = 1_500
    export_max_rows: int = 50_000
    pseudonym_secret: str = ""
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
    def pseudonym_key(self) -> bytes:
        """Return the HMAC key for customer pseudonyms (derived from JWT_SECRET when unset)."""
        if self.pseudonym_secret:
            return self.pseudonym_secret.encode()
        return f"pseudonym:{self.jwt_secret}".encode()

    @property
    def cors_origin_list(self) -> list[str]:
        """Return CORS_ORIGINS as a list of non-empty origins."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def warn_derived_pseudonym_secret(settings: Settings) -> bool:
    """Log one warning when customer pseudonyms are keyed from JWT_SECRET; return whether it did."""
    if settings.pseudonym_secret:
        return False
    structlog.get_logger().warning(
        "pseudonym_secret_derived_from_jwt",
        hint="set PSEUDONYM_SECRET in production: changing JWT_SECRET changes every pseudonym",
    )
    return True


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
