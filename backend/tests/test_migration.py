from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
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


def test_migration_0002_backfills_refresh_token_tenant_id(empty_url: URL) -> None:
    cfg = alembic_config(empty_url)
    command.upgrade(cfg, "0001")
    engine = create_engine(empty_url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO tenants (id, name) VALUES (gen_random_uuid(), 'T')"))
        conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, password_hash, full_name, role) "
                "SELECT gen_random_uuid(), id, 'a@example.com', 'x', 'A', 'owner' FROM tenants"
            )
        )
        conn.execute(
            text(
                "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at) "
                "SELECT gen_random_uuid(), id, 'h', now() FROM users"
            )
        )
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        mismatched = conn.execute(
            text(
                "SELECT count(*) FROM refresh_tokens r JOIN users u ON u.id = r.user_id "
                "WHERE r.tenant_id IS DISTINCT FROM u.tenant_id"
            )
        ).scalar()
        total = conn.execute(text("SELECT count(*) FROM refresh_tokens")).scalar()
    assert total == 1
    assert mismatched == 0
    column = next(
        c for c in inspect(engine).get_columns("refresh_tokens") if c["name"] == "tenant_id"
    )
    assert column["nullable"] is False
    command.downgrade(cfg, "0001")
    engine.dispose()
