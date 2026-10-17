"""Shared fixtures: a real Postgres database built by Alembic migrations."""

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

DEFAULT_URL = "postgresql+psycopg://analyst:analyst@localhost:5432/analyst"
TEST_DB = "analyst_test"
ROOT = Path(__file__).resolve().parents[1]

_base_url = make_url(os.environ.get("DATABASE_URL", DEFAULT_URL))
TEST_URL = _base_url.set(database=TEST_DB)

# Must be set before the app (and its cached settings) are imported.
os.environ.update(
    {
        "DATABASE_URL": TEST_URL.render_as_string(hide_password=False),
        "JWT_SECRET": "test-secret-with-more-than-thirty-two-bytes",
        "JWT_ALGORITHM": "HS256",
        "ACCESS_TOKEN_MINUTES": "30",
        "COOKIE_SECURE": "true",
        "RATE_LIMIT_ENABLED": "false",
        "AUTO_ANALYZE": "false",
        "CORS_ORIGINS": "http://localhost:3000",
        "STORAGE_DIR": tempfile.mkdtemp(prefix="analyst-test-storage-"),
    }
)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.session import get_engine, get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from app.workers.celery_app import celery_app  # noqa: E402

# Tasks run inline in tests (no broker); failures are recorded by the task itself.
celery_app.conf.task_always_eager = True


def recreate_database(name: str) -> None:
    """Drop and create an empty database."""
    admin = create_engine(_base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()


def drop_database(name: str) -> None:
    """Drop a database if it exists."""
    admin = create_engine(_base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def alembic_config(url: URL) -> Config:
    """Alembic configuration pointed at `url`."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    escaped = url.render_as_string(hide_password=False).replace("%", "%%")
    cfg.set_main_option("sqlalchemy.url", escaped)
    return cfg


@pytest.fixture(scope="session")
def database() -> Iterator[URL]:
    """Create the test database and migrate it to head."""
    recreate_database(TEST_DB)
    command.upgrade(alembic_config(TEST_URL), "head")
    yield TEST_URL
    get_engine().dispose()


@pytest.fixture
def db(database: URL) -> Iterator[Session]:
    """Empty all tables, then provide a session for direct DB assertions."""
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "TRUNCATE tenants, users, refresh_tokens, datasets, dataset_versions, column_profiles, cleaning_logs, analysis_runs, insights RESTART IDENTITY CASCADE"
            )
        )
    with get_sessionmaker()() as session:
        yield session


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    """HTTPS test client (so Secure cookies round-trip) that returns 500s instead of raising."""
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()
