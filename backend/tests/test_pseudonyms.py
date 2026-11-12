"""Customer-id pseudonyms toward the model: format, stability, tenant separation, the map."""

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import pseudonyms
from app.core.config import Settings, get_settings
from app.core.pseudonyms import PseudonymMap, customer_code, load_pseudonym_map
from app.db.repository import DatasetVersionRepository
from tests.dataset_helpers import csv_bytes, upload_ready
from tests.helpers import register

KEY = b"k" * 32
CODE = re.compile(r"^CUST-[0-9A-HJKMNP-TV-Z]{8}$")


def test_code_format_is_short_and_readable() -> None:
    assert CODE.match(customer_code(KEY, "t1", "d1", "C-001"))


def test_code_is_stable_per_tenant_and_dataset() -> None:
    first = customer_code(KEY, "t1", "d1", "C-001")
    assert customer_code(KEY, "t1", "d1", "C-001") == first
    assert customer_code(KEY, "t2", "d1", "C-001") != first
    assert customer_code(KEY, "t1", "d2", "C-001") != first
    assert customer_code(b"other" * 8, "t1", "d1", "C-001") != first


def test_map_round_trips_and_reads_typed_tokens() -> None:
    mapping = PseudonymMap.build(KEY, "t1", "d1", ["C-001", "c-002", "10234"])
    code = mapping.to_pseudonym("C-001")
    assert code is not None and mapping.to_real(code) == "C-001"
    assert mapping.lookup_token(" c-001 ") == code
    assert mapping.lookup_token("C-002") == mapping.to_pseudonym("c-002")
    assert mapping.lookup_token("10234") == mapping.to_pseudonym("10234")
    assert mapping.lookup_token("C-999") is None
    assert mapping.pattern.fullmatch(code)


def test_map_lengthens_colliding_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pseudonyms, "SHORT_LENGTH", 1)
    values = [f"C{i}" for i in range(200)]
    mapping = PseudonymMap.build(KEY, "t1", "d1", values)
    codes = [mapping.to_pseudonym(v) for v in values]
    assert len(set(codes)) == len(values)
    assert all(mapping.to_real(c) == v for c, v in zip(codes, values, strict=True))  # type: ignore[arg-type]


def test_map_carries_the_legacy_form_of_stored_rows() -> None:
    from app.ml.modules.segments import pseudonym as legacy

    mapping = PseudonymMap.build(KEY, "t1", "d1", ["C-001"])
    assert mapping.from_stored(legacy(KEY, "t1", "C-001")) == mapping.to_pseudonym("C-001")
    assert mapping.from_stored("C-001") == mapping.to_pseudonym("C-001")
    assert mapping.real_from_stored(legacy(KEY, "t1", "C-001")) == "C-001"


@pytest.mark.parametrize("secret", ["", "change-me", "short"])
def test_production_refuses_a_missing_or_weak_pseudonym_secret(secret: str) -> None:
    with pytest.raises(ValueError, match="PSEUDONYM_SECRET"):
        Settings(
            app_env="production", jwt_secret="j" * 32, pseudonym_secret=secret
        ).ensure_production_safe()
    Settings(
        app_env="development", jwt_secret="j" * 32, pseudonym_secret=""
    ).ensure_production_safe()
    Settings(
        app_env="production", jwt_secret="j" * 32, pseudonym_secret="p" * 32
    ).ensure_production_safe()


def test_loaded_map_differs_per_tenant_and_is_cached(client: TestClient, db: Session) -> None:
    rows = [[f"2024-0{1 + i % 9}-10", f"C{i % 7:03d}", "Pen", 1, 2.0, 2.0] for i in range(30)]
    header = ["order_date", "customer_id", "product", "quantity", "unit_price", "revenue"]
    first, second = register(client), register(client)
    maps = []
    for account in (first, second):
        dataset = upload_ready(client, account, csv_bytes(rows, header), "c.csv")
        tenant = uuid.UUID(account.tenant_id)
        version = DatasetVersionRepository(db, tenant).get(uuid.UUID(dataset["versions"][0]["id"]))
        maps.append(load_pseudonym_map(db, tenant, version, get_settings()))
    a, b = maps
    assert a is not None and b is not None
    assert a.column == "customer_id" and len(a) == 7
    assert a.to_pseudonym("C000") != b.to_pseudonym("C000")
    assert b.to_real(a.to_pseudonym("C000") or "") is None


def test_worker_and_beat_refuse_a_weak_secret_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from celery import signals

    from app.workers import celery_app

    weak = Settings(app_env="production", jwt_secret="j" * 32, pseudonym_secret="")
    monkeypatch.setattr(celery_app, "get_settings", lambda: weak)
    with pytest.raises(ValueError, match="PSEUDONYM_SECRET"):
        celery_app.startup_checks()
    receivers = [r[1]() for r in signals.beat_init.receivers]
    assert celery_app.startup_checks in receivers
