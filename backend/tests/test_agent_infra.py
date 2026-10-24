"""Agent infrastructure: sandbox client errors and data packaging, stream slots (Redis/memory)."""

import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.agent.sandbox_client import SandboxClient, SandboxData, SandboxError, build_data
from app.agent.streams import MemoryStreamSlots, RedisStreamSlots, get_stream_slots
from app.core.config import get_settings
from app.core.errors import NotFoundError, RateLimitedError
from app.db.repository import DatasetVersionRepository
from app.models.profile import ColumnProfile
from tests.agent_helpers import pii_csv
from tests.dataset_helpers import csv_bytes, sample, upload_ready
from tests.helpers import register

DATA = SandboxData(csv="a\n1\n", dtypes={"a": "Int64"}, columns=["a"], rows=1, total_rows=1)


def sandbox(handler: Any) -> SandboxClient:
    return SandboxClient("http://sandbox.test", 1, transport=httpx.MockTransport(handler))


def raises(exc: type[Exception]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc("boom", request=request)  # type: ignore[call-arg]

    return handler


@pytest.mark.parametrize(
    ("handler", "code"),
    [
        (lambda r: httpx.Response(429), "sandbox_busy"),
        (lambda r: httpx.Response(500), "sandbox_error"),
        (lambda r: httpx.Response(200, content=b"not json"), "sandbox_error"),
        (raises(httpx.ConnectError), "sandbox_unavailable"),
        (raises(httpx.ReadTimeout), "sandbox_timeout"),
    ],
)
def test_sandbox_client_maps_failures(handler: Any, code: str) -> None:
    client = sandbox(handler)
    with pytest.raises(SandboxError) as caught:
        client.run("result = 1", DATA)
    assert caught.value.code == code
    client.close()


def test_sandbox_client_sends_code_and_data() -> None:
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        assert request.url.path == "/run"
        return httpx.Response(200, json={"ok": True, "result": 1})

    assert sandbox(handler).run("result = 1", DATA) == {"ok": True, "result": 1}
    assert b'"data_csv": "a\\n1\\n"' in seen[0]
    unix = SandboxClient("unix:///ipc/sandbox.sock", 1)  # builds a uds transport, no connection yet
    unix.close()


def version_of(db: Session, account: Any, dataset: dict[str, Any]) -> Any:
    repo = DatasetVersionRepository(db, uuid.UUID(account.tenant_id))
    return repo.get(uuid.UUID(dataset["versions"][0]["id"]))


def test_build_data_drops_personal_data_and_samples_large_data(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(client)
    dataset = upload_ready(client, owner, pii_csv(), "people.csv")
    version = version_of(db, owner, dataset)
    settings = get_settings()
    data = build_data(db, uuid.UUID(owner.tenant_id), version, settings, allow_pii=False)
    # Email and phone are not role columns: they never reach the sandbox, opted in or not.
    assert "customer_email" not in data.columns and "@" not in data.csv
    assert data.rows == data.total_rows == 60 and not data.warnings
    opened = build_data(db, uuid.UUID(owner.tenant_id), version, settings, allow_pii=True)
    assert "@" not in opened.csv

    rows = [[f"2024-0{1 + i % 9}-10", f"C{i % 7:03d}", "Pen", 1, 2.0, 2.0] for i in range(30)]
    header = ["order_date", "customer_id", "product", "quantity", "unit_price", "revenue"]
    customers = upload_ready(client, owner, csv_bytes(rows, header), "customers.csv")
    customer_version = version_of(db, owner, customers)
    db.execute(
        update(ColumnProfile)
        .where(
            ColumnProfile.dataset_version_id == customer_version.id,
            ColumnProfile.name == "customer_id",
        )
        .values(pii_flag=True)
    )
    db.commit()
    hidden = build_data(db, uuid.UUID(owner.tenant_id), customer_version, settings, allow_pii=False)
    assert (
        hidden.pseudonymised == ["customer_id"]
        and "C000" not in hidden.csv
        and "cust_" in hidden.csv
    )
    assert hidden.dtypes["customer_id"] == "string"
    shown = build_data(db, uuid.UUID(owner.tenant_id), customer_version, settings, allow_pii=True)
    assert "C000" in shown.csv and shown.pseudonymised == []

    big = upload_ready(client, owner, sample("retail_clean.csv"), "retail.csv")
    monkeypatch.setattr(settings, "sandbox_max_upload_mb", 0.002)
    small = build_data(
        db, uuid.UUID(owner.tenant_id), version_of(db, owner, big), settings, allow_pii=False
    )
    assert small.rows < small.total_rows == 400
    assert "random sample" in small.warnings[0] and len(small.csv) <= 0.002 * 1024 * 1024 * 1.2


def test_build_data_refuses_another_tenants_version(client: TestClient, db: Session) -> None:
    owner = register(client)
    attacker = register(client)
    dataset = upload_ready(client, owner, sample("retail_clean.csv"), "retail.csv")
    version = version_of(db, owner, dataset)
    with pytest.raises(NotFoundError):
        build_data(db, uuid.UUID(attacker.tenant_id), version, get_settings(), allow_pii=True)


def test_memory_slots_limit_expire_and_release(monkeypatch: pytest.MonkeyPatch) -> None:
    slots = MemoryStreamSlots()
    user = uuid.uuid4()
    first = slots.acquire(user, 1)
    with pytest.raises(RateLimitedError) as caught:
        slots.acquire(user, 1)
    assert caught.value.code == "too_many_streams"
    slots.release(user, first)
    slots.release(user, first)  # idempotent
    slots.acquire(user, 1)
    monkeypatch.setattr("app.agent.streams.STREAM_TTL_S", -1)  # every slot is now expired
    slots.acquire(user, 1)


def test_redis_slots_against_the_real_redis() -> None:
    slots = RedisStreamSlots(get_settings().redis_url)
    user = uuid.uuid4()
    try:
        tokens = [slots.acquire(user, 2), slots.acquire(user, 2)]
    except RateLimitedError:  # pragma: no cover - only if a stale key existed
        raise
    if any(t.startswith("mem:") for t in tokens):
        pytest.skip("Redis is not reachable from this test run")
    with pytest.raises(RateLimitedError):
        slots.acquire(user, 2)
    for token in tokens:
        slots.release(user, token)
    slots.release(user, slots.acquire(user, 2))


def test_redis_slots_fall_back_to_memory_when_redis_is_down() -> None:
    slots = RedisStreamSlots("redis://127.0.0.1:1/0")
    user = uuid.uuid4()
    token = slots.acquire(user, 1)
    assert token.startswith("mem:")
    with pytest.raises(RateLimitedError):
        slots.acquire(user, 1)
    slots.release(user, token)
    slots.release(user, "redis-token")  # Redis down: logged, no error


def test_slot_store_follows_the_rate_limit_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    get_stream_slots.cache_clear()
    assert isinstance(get_stream_slots(), MemoryStreamSlots)  # RATE_LIMIT_ENABLED=false in tests
    get_stream_slots.cache_clear()
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    assert isinstance(get_stream_slots(), RedisStreamSlots)
    get_stream_slots.cache_clear()
