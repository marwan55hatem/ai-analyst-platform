"""What the model receives: customer ids are codes in SQL, Python and stored-result tools."""

import json
import re
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.agent import service as chat_service
from app.core.config import get_settings
from app.core.pseudonyms import load_pseudonym_map
from app.db.repository import DatasetVersionRepository
from app.llm.fake import FakeLLMClient, FakeTurn
from app.models.profile import ColumnProfile
from tests.agent_helpers import FakeSandbox, ask, conversation
from tests.dataset_helpers import csv_bytes, upload_ready
from tests.helpers import API, register

CODE = re.compile(r"^CUST-[0-9A-HJKMNP-TV-Z]{8,10}$")
HEADER = ["order_date", "customer_id", "product", "quantity", "unit_price", "revenue"]
IDS = ["7001", "7002", "7003", "7004", "7005"]


def numeric_ids_csv() -> bytes:
    """Integer customer ids, and revenue values equal to some of those ids."""
    rows = []
    for i in range(40):
        customer = IDS[i % 5]
        revenue = float(IDS[(i + 1) % 5]) if i % 4 == 0 else 3.0
        rows.append([f"2024-{1 + i % 12:02d}-10", customer, "Pen", 1, revenue, revenue])
    return csv_bytes(rows, HEADER)


def expected_totals() -> dict[str, float]:
    totals: dict[str, float] = {}
    for i in range(40):
        revenue = float(IDS[(i + 1) % 5]) if i % 4 == 0 else 3.0
        totals[IDS[i % 5]] = totals.get(IDS[i % 5], 0.0) + revenue
    return totals


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> FakeSandbox:
    fake = FakeSandbox()
    monkeypatch.setattr(chat_service, "_sandbox", lambda url, timeout: fake)
    return fake


def model_tool_outputs(model: FakeLLMClient) -> list[dict[str, Any]]:
    """Decode every tool message the model was sent."""
    seen = model.chat_calls[-1].messages
    outputs = []
    for message in seen:
        if message.role == "tool":
            body = message.content
            outputs.append(json.loads(body[body.index("{") : body.rindex("}") + 1]))
    return outputs


def setup(client: TestClient, db: Session) -> tuple[Any, dict[str, Any], Any]:
    owner = register(client)
    dataset = upload_ready(client, owner, numeric_ids_csv(), "numbers.csv")
    tenant = uuid.UUID(owner.tenant_id)
    version = DatasetVersionRepository(db, tenant).get(uuid.UUID(dataset["versions"][0]["id"]))
    mapping = load_pseudonym_map(db, tenant, version, get_settings())
    assert mapping is not None and mapping.column == "customer_id"
    return owner, dataset, mapping


def test_numeric_customer_ids_do_not_touch_numbers(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    owner, dataset, mapping = setup(client, db)
    code = mapping.to_pseudonym("7003")
    sql = "SELECT customer_id, SUM(revenue) AS total FROM data GROUP BY 1 ORDER BY 1"
    where = f"SELECT COUNT(*) AS orders FROM data WHERE customer_id = '{code}'"
    model = FakeLLMClient(
        turns=[
            FakeTurn(tool_calls=[("run_sql", {"sql": sql}), ("run_sql", {"sql": where})]),
            FakeTurn(text="done"),
        ]
    )
    monkeypatch.setattr(chat_service, "get_llm_client", lambda: model)
    ask(client, owner, conversation(client, owner, dataset["id"]), "who spends most?")
    grouped, counted = model_tool_outputs(model)
    totals = {mapping.to_real(row[0]): row[1] for row in grouped["rows"]}
    assert all(CODE.match(row[0]) for row in grouped["rows"])
    assert totals == expected_totals()
    assert counted["rows"] == [[8]]
    sent = json.dumps([m.content for m in model.chat_calls[-1].messages if m.role == "tool"])
    assert '"7003"' not in sent and "'7003'" not in sent


def test_python_always_gets_codes_even_when_personal_data_is_allowed(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    owner, dataset, mapping = setup(client, db)
    client.put(f"{API}/company/profile", headers=owner.headers, json={"allow_pii": True})
    model = FakeLLMClient(
        turns=[
            FakeTurn(tool_calls=[("run_python", {"code": "result = df.shape[0]"})]),
            FakeTurn(text="done"),
        ]
    )
    monkeypatch.setattr(chat_service, "get_llm_client", lambda: model)
    ask(client, owner, conversation(client, owner, dataset["id"]), "count?")
    sent = sandbox.calls[-1][1]
    customers = sent.csv.splitlines()[1:]
    position = sent.columns.index("customer_id")
    values = {line.split(",")[position] for line in customers}
    assert values == {mapping.to_pseudonym(i) for i in IDS}
    assert sent.dtypes["customer_id"] == "string"


def test_flagged_customer_id_is_readable_as_codes_in_sql(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    owner, dataset, mapping = setup(client, db)
    db.execute(
        update(ColumnProfile).where(ColumnProfile.name == "customer_id").values(pii_flag=True)
    )
    db.commit()
    model = FakeLLMClient(
        turns=[
            FakeTurn(tool_calls=[("run_sql", {"sql": "SELECT DISTINCT customer_id FROM data"})]),
            FakeTurn(text="done"),
        ]
    )
    monkeypatch.setattr(chat_service, "get_llm_client", lambda: model)
    ask(client, owner, conversation(client, owner, dataset["id"]), "list customers")
    (result,) = model_tool_outputs(model)
    assert sorted(r[0] for r in result["rows"]) == sorted(mapping.to_pseudonym(i) for i in IDS)


def test_a_role_change_recodes_the_new_customer_column(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox
) -> None:
    """Review finding: the cached map must follow the column that has the customer_id role."""
    rows = [
        [f"2024-0{1 + i % 9}-10", f"C{i % 5:03d}", f"client-{i % 4}", "Pen", 1, 2.0, 2.0]
        for i in range(40)
    ]
    header = ["order_date", "customer_id", "buyer", "product", "quantity", "unit_price", "revenue"]
    owner = register(client)
    dataset = upload_ready(client, owner, csv_bytes(rows, header), "two.csv")
    sql = {"sql": "SELECT DISTINCT customer_id, buyer FROM data"}

    def ask_sql() -> list[list[Any]]:
        model = FakeLLMClient(
            turns=[FakeTurn(tool_calls=[("run_sql", sql)]), FakeTurn(text="done")]
        )
        monkeypatch.setattr(chat_service, "get_llm_client", lambda: model)
        ask(client, owner, conversation(client, owner, dataset["id"]), "who?")
        (result,) = model_tool_outputs(model)
        rows_seen: list[list[Any]] = result["rows"]
        return rows_seen

    before = ask_sql()
    assert all(CODE.match(r[0]) and r[1].startswith("client-") for r in before)
    for name, role in (("customer_id", "other"), ("buyer", "customer_id")):
        response = client.put(
            f"{API}/datasets/{dataset['id']}/columns/{name}/role",
            headers=owner.headers,
            json={"role": role},
        )
        assert response.status_code == 200, response.text
    after = ask_sql()
    assert all(r[0].startswith("C0") and CODE.match(r[1]) for r in after), after


def test_a_corrupt_map_file_is_rebuilt(client: TestClient, db: Session) -> None:
    """Review finding: a half-written map file must not break every query on the version."""
    from app.core import pseudonyms
    from app.services.storage import get_storage

    owner, dataset, mapping = setup(client, db)
    tenant = uuid.UUID(owner.tenant_id)
    version = DatasetVersionRepository(db, tenant).get(uuid.UUID(dataset["versions"][0]["id"]))
    path = pseudonyms.map_file_path(db, tenant, version, get_settings())
    assert path is not None
    with open(path, "wb") as handle:
        handle.write(b"PAR1 half written")
    pseudonyms.clear_cache()
    rebuilt = pseudonyms.map_file_path(db, tenant, version, get_settings())
    import pandas as pd

    assert rebuilt is not None and len(pd.read_parquet(rebuilt)) == len(mapping)
    assert get_storage().exists(str(version.parquet_path))
