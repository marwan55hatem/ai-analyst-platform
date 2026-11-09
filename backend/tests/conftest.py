"""Shared fixtures: a real Postgres database built by Alembic migrations."""

import atexit
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

DEFAULT_URL = "postgresql+psycopg://analyst:analyst@localhost:5432/analyst"
ROOT = Path(__file__).resolve().parents[1]

# pytest-xdist sets PYTEST_XDIST_WORKER (gw0, gw1, ...); a serial run has none.
# Each worker gets its own database so TRUNCATE in one never touches another.
WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
WORKER_SUFFIX = f"_{WORKER}" if WORKER else ""
TEST_DB = f"analyst_test{WORKER_SUFFIX}"

_base_url = make_url(os.environ.get("DATABASE_URL", DEFAULT_URL))
TEST_URL = _base_url.set(database=TEST_DB)

_STORAGE = tempfile.mkdtemp(prefix=f"analyst-test-storage{WORKER_SUFFIX}-")
_MLFLOW = tempfile.mkdtemp(prefix=f"analyst-test-mlflow{WORKER_SUFFIX}-")
for _d in (_STORAGE, _MLFLOW):
    atexit.register(shutil.rmtree, _d, ignore_errors=True)

# WeasyPrint needs Pango; on Windows it comes from MSYS2 (see docs/reports.md). Linux finds it.
_MSYS_PANGO = Path("C:/msys64/ucrt64/bin")
if os.name == "nt" and "WEASYPRINT_DLL_DIRECTORIES" not in os.environ and _MSYS_PANGO.is_dir():
    os.environ["WEASYPRINT_DLL_DIRECTORIES"] = str(_MSYS_PANGO)

# Must be set before the app (and its cached settings) are imported.
os.environ.update(
    {
        "DATABASE_URL": TEST_URL.render_as_string(hide_password=False),
        "JWT_SECRET": "test-secret-with-more-than-thirty-two-bytes",
        "JWT_ALGORITHM": "HS256",
        "ACCESS_TOKEN_MINUTES": "30",
        "COOKIE_SECURE": "true",
        "RATE_LIMIT_ENABLED": "false",
        # the pytest process is bigger than a worker; memory-guard tests inject their own RSS
        "FORECAST_MEMORY_MB": "100000",
        "AUTO_ANALYZE": "false",
        "CORS_ORIGINS": "http://localhost:3000",
        "STORAGE_DIR": _STORAGE,
        # forecasts log to a throwaway local store, never to a server
        "MLFLOW_TRACKING_URI": Path(_MLFLOW).as_uri(),
        # Never reach a real model provider: the developer's .env may hold a real key.
        "APP_ENV": "test",
        "LLM_PROVIDER": "fake",
        "LLM_API_KEY": "",
        "GROQ_API_KEY": "",
        "LLM_BASE_URL": "http://llm.invalid",
        "SANDBOX_URL": "http://sandbox.invalid:1",
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


def _pango_problem() -> str | None:
    """Return why WeasyPrint cannot load Pango here, or None when PDF rendering works."""
    try:
        import weasyprint  # noqa: F401  (raises OSError when the Pango libraries are missing)
    except (OSError, ImportError) as exc:
        return f"WeasyPrint cannot load Pango ({exc.__class__.__name__}); see docs/reports.md"
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip the PDF tests with a reason when Pango is missing (Docker and CI always have it)."""
    reason = _pango_problem()
    if reason is None:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if item.path.name == "test_reports.py":
            item.add_marker(skip)


@pytest.fixture(scope="session")
def database() -> Iterator[URL]:
    """Create the test database and migrate it to head."""
    recreate_database(TEST_DB)
    command.upgrade(alembic_config(TEST_URL), "head")
    yield TEST_URL
    get_engine().dispose()
    if WORKER:
        drop_database(TEST_DB)


@pytest.fixture
def db(database: URL) -> Iterator[Session]:
    """Empty all tables, then provide a session for direct DB assertions."""
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "TRUNCATE tenants, users, refresh_tokens, datasets, dataset_versions, column_profiles, cleaning_logs, analysis_runs, insights, model_runs, segment_assignments, churn_scores, business_profiles, conversations, messages, agent_usage_daily, reports, alerts, alert_settings RESTART IDENTITY CASCADE"
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
