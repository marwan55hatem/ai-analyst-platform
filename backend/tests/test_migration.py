from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL

from tests.conftest import _base_url, alembic_config, drop_database, recreate_database

DB = "analyst_migration_test"


@pytest.fixture
def empty_url() -> Iterator[URL]:
    recreate_database(DB)
    yield _base_url.set(database=DB)
    drop_database(DB)


def test_migration_creates_schema_and_is_safe_to_run_twice(empty_url: URL) -> None:
    cfg = alembic_config(empty_url)
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    engine = create_engine(empty_url)
    tables = set(inspect(engine).get_table_names())
    assert {"tenants", "users", "refresh_tokens", "alembic_version"} <= tables
    user_columns = {c["name"] for c in inspect(engine).get_columns("users")}
    assert {
        "id",
        "tenant_id",
        "email",
        "password_hash",
        "full_name",
        "role",
        "is_active",
    } <= user_columns
    engine.dispose()
    command.downgrade(cfg, "base")
    engine = create_engine(empty_url)
    assert "users" not in inspect(engine).get_table_names()
    engine.dispose()
