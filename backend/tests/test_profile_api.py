"""Profile stage and cleaning through the HTTP API (Celery runs inline in tests)."""

import hashlib
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.repository import CleaningLogRepository
from app.models.dataset import Dataset, DatasetVersion
from app.services import cleaning, storage
from app.services.query import SafeQueryService
from tests.dataset_helpers import csv_bytes, upload, upload_ready
from tests.helpers import API, Account, add_member, register
from tests.retail_fixtures import build_csv

RECOMMENDED = [
    "trim:product",
    "trim:region",
    "cast:qty",
    "cast:unit_price",
    "cast:discount",
    "cast:revenue",
    "parse_dates:order_date",
    "case:product",
    "case:category",
    "case:region",
    "case:order_status",
]


@pytest.fixture
def owner(client: TestClient) -> Account:
    return register(client)


@pytest.fixture
def messy(client: TestClient, owner: Account) -> dict[str, Any]:
    return upload_ready(client, owner, build_csv("messy"), "messy.csv")


def _url(dataset: dict[str, Any], tail: str) -> str:
    return f"{API}/datasets/{dataset['id']}/{tail}"


def _apply(
    client: TestClient, owner: Account, dataset: dict[str, Any], fixes: list[dict[str, Any]]
) -> Any:
    return client.post(
        _url(dataset, "cleaning-plan/apply"), headers=owner.headers, json={"fixes": fixes}
    )


def _fixes(*ids: str) -> list[dict[str, Any]]:
    return [{"id": i} for i in ids]


def test_profile_runs_automatically_after_upload(messy: dict[str, Any]) -> None:
    version = messy["versions"][0]
    assert version["status"] == "ready"
    assert version["profile_status"] == "ready"
    assert version["profile_error"] is None
    assert isinstance(version["quality_score"], int) and version["quality_score"] < 60
    assert version["source_version_id"] is None


def test_get_profile(client: TestClient, owner: Account, messy: dict[str, Any]) -> None:
    body = client.get(_url(messy, "profile"), headers=owner.headers).json()
    assert body["version"] == 1 and body["profile_status"] == "ready"
    assert body["row_count"] == 1650 and body["column_count"] == 17
    names = {c["name"]: c for c in body["columns"]}
    assert names["qty"]["semantic_role"] == "quantity" and names["qty"]["role_source"] == "rule"
    assert names["customer_email"]["pii_flag"] is True
    assert names["unit_price"]["stats"]["stored_as"] == "text"
    assert body["dataset"]["duplicate_rows"] == 150
    assert "email" in body["dataset"]["roles"]
    emails = names["customer_email"]["stats"]["top_values"]
    assert emails and all("example.com" not in t["value"] for t in emails)


def test_get_quality(client: TestClient, owner: Account, messy: dict[str, Any]) -> None:
    body = client.get(_url(messy, "quality"), headers=owner.headers).json()
    assert body["score"] == messy["versions"][0]["quality_score"] < 60
    assert set(body["breakdown"]) == {
        "completeness",
        "validity",
        "uniqueness",
        "consistency",
        "sufficiency",
    }
    assert body["max"]["completeness"] == 30
    assert len(body["issues"]) == 5
    assert body["confidence"] == "high" and body["sample_size"] == 1650
    first = body["issues"][0]
    assert {"title", "detail", "suggested_fix", "points_lost", "fix_id"} <= set(first)


def test_get_cleaning_plan_marks_only_low_risk_fixes_as_recommended(
    client: TestClient, owner: Account, messy: dict[str, Any]
) -> None:
    body = client.get(_url(messy, "cleaning-plan"), headers=owner.headers).json()
    fixes = {f["id"]: f for f in body["fixes"]}
    assert {i for i, f in fixes.items() if f["recommended"]} == set(RECOMMENDED)
    assert fixes["dedupe"]["risk"] == "medium" and not fixes["dedupe"]["recommended"]
    assert fixes["parse_dates:delivery_date"]["risk"] == "high"
    assert fixes["nulls:cost"]["default_strategy"] == "keep"
    # nothing was applied: still a single version
    dataset = client.get(_url(messy, ""), headers=owner.headers).json()
    assert len(dataset["versions"]) == 1


def test_unknown_version_and_bad_version_numbers(
    client: TestClient, owner: Account, messy: dict[str, Any]
) -> None:
    assert client.get(_url(messy, "profile?version=9"), headers=owner.headers).status_code == 404
    assert client.get(_url(messy, "profile?version=0"), headers=owner.headers).status_code == 422
    assert client.get(_url(messy, "profile?version=1"), headers=owner.headers).status_code == 200


def test_profile_is_not_ready_while_pending_or_running(
    client: TestClient, db: Session, owner: Account, messy: dict[str, Any]
) -> None:
    version = db.get(DatasetVersion, uuid.UUID(messy["versions"][0]["id"]))
    assert version is not None
    for status in ("pending", "running"):
        version.profile_status = status
        db.commit()
        for tail in ("profile", "quality", "cleaning-plan"):
            r = client.get(_url(messy, tail), headers=owner.headers)
            assert r.status_code == 409 and r.json()["code"] == "profile_not_ready", tail
        r = _apply(client, owner, messy, _fixes("trim:region"))
        assert r.status_code == 409


def test_a_failed_profile_does_not_block_the_dataset(
    client: TestClient, owner: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("internal detail /secret/path")

    monkeypatch.setattr("app.services.profiling.service.compute_profile", boom)
    made = upload_ready(client, owner, csv_bytes([[1, "a"], [2, "b"]], ["n", "label"]), "x.csv")
    version = made["versions"][0]
    assert version["status"] == "ready" and version["profile_status"] == "failed"
    assert "secret" not in (version["profile_error"] or "")
    r = client.get(_url(made, "profile"), headers=owner.headers)
    assert r.status_code == 409 and r.json()["code"] == "profile_failed"
    preview = client.get(_url(made, "preview"), headers=owner.headers)
    assert preview.status_code == 200 and preview.json()["total_rows"] == 2
    monkeypatch.undo()
    rerun = client.post(_url(made, "profile/rerun"), headers=owner.headers)
    assert rerun.status_code == 202 and rerun.json()["profile_status"] == "ready"


def test_profile_enqueue_failure_marks_only_the_profile_failed(
    client: TestClient, owner: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workers import tasks

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("redis is down")

    monkeypatch.setattr(tasks.profile_dataset, "delay", broken)
    made = upload_ready(client, owner, csv_bytes([[1, "a"]], ["n", "label"]), "x.csv")
    version = made["versions"][0]
    assert version["status"] == "ready" and version["profile_status"] == "failed"
    assert "could not be started" in version["profile_error"]


def test_role_override(client: TestClient, owner: Account, messy: dict[str, Any]) -> None:
    url = _url(messy, "columns/cost/role")
    r = client.put(url, headers=owner.headers, json={"role": "discount"})
    assert r.status_code == 200
    assert r.json()["semantic_role"] == "discount"
    assert r.json()["role_source"] == "user" and r.json()["role_confidence"] == 1.0
    profile = client.get(_url(messy, "profile"), headers=owner.headers).json()
    cost = next(c for c in profile["columns"] if c["name"] == "cost")
    assert cost["role_source"] == "user"


def test_role_override_errors(client: TestClient, owner: Account, messy: dict[str, Any]) -> None:
    bad_role = client.put(
        _url(messy, "columns/cost/role"), headers=owner.headers, json={"role": "banana"}
    )
    assert bad_role.status_code == 422 and bad_role.json()["code"] == "validation_error"
    no_column = client.put(
        _url(messy, "columns/nope/role"), headers=owner.headers, json={"role": "cost"}
    )
    assert no_column.status_code == 404


def test_any_member_can_override_roles_and_apply_cleaning(
    client: TestClient, owner: Account, messy: dict[str, Any]
) -> None:
    member = add_member(client, owner, "member")
    r = client.put(
        _url(messy, "columns/cost/role"), headers=member.headers, json={"role": "revenue"}
    )
    assert r.status_code == 200
    applied = _apply(client, member, messy, _fixes("trim:region"))
    assert applied.status_code == 202


def test_rerun_keeps_user_roles_and_refreshes_the_profile(
    client: TestClient, owner: Account, messy: dict[str, Any]
) -> None:
    client.put(_url(messy, "columns/cost/role"), headers=owner.headers, json={"role": "discount"})
    rerun = client.post(_url(messy, "profile/rerun"), headers=owner.headers)
    assert rerun.status_code == 202
    assert rerun.json()["profile_status"] == "ready"
    profile = client.get(_url(messy, "profile"), headers=owner.headers).json()
    cost = next(c for c in profile["columns"] if c["name"] == "cost")
    assert (cost["semantic_role"], cost["role_source"]) == ("discount", "user")
    assert len(profile["columns"]) == 17  # old rows were replaced, not duplicated


def test_rerun_of_a_failed_profile(
    client: TestClient, db: Session, owner: Account, messy: dict[str, Any]
) -> None:
    version = db.get(DatasetVersion, uuid.UUID(messy["versions"][0]["id"]))
    assert version is not None
    version.profile_status, version.profile_error = "failed", "boom"
    db.commit()
    r = client.post(_url(messy, "profile/rerun"), headers=owner.headers)
    assert r.status_code == 202
    assert r.json()["profile_status"] == "ready" and r.json()["profile_error"] is None


def test_rerun_while_running_is_refused(
    client: TestClient, db: Session, owner: Account, messy: dict[str, Any]
) -> None:
    version = db.get(DatasetVersion, uuid.UUID(messy["versions"][0]["id"]))
    assert version is not None
    version.profile_status = "running"
    db.commit()
    r = client.post(_url(messy, "profile/rerun"), headers=owner.headers)
    assert r.status_code == 409 and r.json()["code"] == "profile_running"
    db.refresh(version)
    assert version.profile_status == "running"


def test_rerun_a_specific_version(
    client: TestClient, owner: Account, messy: dict[str, Any]
) -> None:
    assert _apply(client, owner, messy, _fixes("trim:region")).status_code == 202
    r = client.post(_url(messy, "profile/rerun?version=1"), headers=owner.headers)
    assert r.status_code == 202 and r.json()["version"] == 1
    assert (
        client.post(_url(messy, "profile/rerun?version=7"), headers=owner.headers).status_code
        == 404
    )


def test_apply_creates_a_new_version_and_leaves_the_original_untouched(
    client: TestClient, db: Session, owner: Account, messy: dict[str, Any]
) -> None:
    v1_id = uuid.UUID(messy["versions"][0]["id"])
    v1 = db.get(DatasetVersion, v1_id)
    assert v1 is not None
    backend = storage.get_storage()
    original_hash = hashlib.sha256(backend.local_path(v1.original_path).read_bytes()).hexdigest()
    parquet_hash = hashlib.sha256(
        backend.local_path(v1.parquet_path or "").read_bytes()
    ).hexdigest()
    snapshot = (v1.row_count, v1.col_count, v1.parquet_path, v1.quality_score, v1.status)

    response = _apply(client, owner, messy, _fixes(*RECOMMENDED))
    assert response.status_code == 202, response.text
    detail = response.json()
    assert [v["version"] for v in detail["versions"]] == [2, 1]
    v2 = detail["versions"][0]
    assert v2["status"] == "ready" and v2["profile_status"] == "ready"
    assert v2["source_version_id"] == str(v1_id)
    assert v2["row_count"] == 1650  # only low-risk fixes: nothing removed
    assert detail["current_version_id"] == v2["id"]

    db.expire_all()
    v1 = db.get(DatasetVersion, v1_id)
    assert v1 is not None
    assert (v1.row_count, v1.col_count, v1.parquet_path, v1.quality_score, v1.status) == snapshot
    assert (
        hashlib.sha256(backend.local_path(v1.original_path).read_bytes()).hexdigest()
        == original_hash
    )
    assert (
        hashlib.sha256(backend.local_path(v1.parquet_path or "").read_bytes()).hexdigest()
        == parquet_hash
    )

    logs = CleaningLogRepository(db, uuid.UUID(owner.tenant_id)).for_version(uuid.UUID(v2["id"]))
    assert {log.fix_id for log in logs} == set(RECOMMENDED)
    assert all(log.rows_affected >= 0 and log.source_version_id == v1_id for log in logs)
    assert sum(log.rows_affected for log in logs) > 1000


def test_new_version_is_profiled_and_queryable(
    client: TestClient, db: Session, owner: Account, messy: dict[str, Any]
) -> None:
    detail = _apply(client, owner, messy, _fixes(*RECOMMENDED)).json()
    v2 = detail["versions"][0]
    assert v2["quality_score"] > messy["versions"][0]["quality_score"]
    preview = client.get(_url(messy, "preview"), headers=owner.headers).json()
    assert preview["version_id"] == v2["id"]
    types = {c["name"]: c["type"] for c in preview["columns"]}
    assert types["unit_price"] == "DOUBLE" and types["order_date"] == "DATE"
    result = SafeQueryService(db).run(
        uuid.UUID(owner.tenant_id), uuid.UUID(v2["id"]), "SELECT count(DISTINCT region) FROM data"
    )
    assert result.rows[0][0] == 5


def test_applying_the_plan_improves_quality_by_25_points(
    client: TestClient, owner: Account, messy: dict[str, Any]
) -> None:
    choices = [
        *_fixes(*RECOMMENDED, "dedupe", "parse_dates:delivery_date"),
        {"id": "nulls:region", "strategy": "impute_mode"},
        {"id": "nulls:channel", "strategy": "impute_mode"},
        {"id": "nulls:qty", "strategy": "impute_median"},
        {"id": "nulls:unit_price", "strategy": "impute_median"},
        {"id": "nulls:revenue", "strategy": "impute_median"},
        {"id": "nulls:cost", "strategy": "impute_median"},
    ]
    assert _apply(client, owner, messy, choices).status_code == 202
    before = client.get(_url(messy, "quality?version=1"), headers=owner.headers).json()
    after = client.get(_url(messy, "quality?version=2"), headers=owner.headers).json()
    assert after["score"] >= before["score"] + 25, (before["breakdown"], after["breakdown"])
    assert after["version"] == 2 and before["version"] == 1
    current = client.get(_url(messy, "quality"), headers=owner.headers).json()
    assert current["version"] == 2


def test_only_listed_fixes_are_applied(
    client: TestClient, owner: Account, messy: dict[str, Any]
) -> None:
    detail = _apply(client, owner, messy, _fixes("trim:region")).json()
    v2 = detail["versions"][0]
    assert v2["row_count"] == 1650
    profile = client.get(_url(messy, "profile?version=2"), headers=owner.headers).json()
    assert profile["dataset"]["duplicate_rows"] == 150  # dedupe was not listed
    cols = {c["name"]: c for c in profile["columns"]}
    assert cols["unit_price"]["stats"]["stored_as"] == "text"
    assert cols["region"]["stats"]["whitespace_count"] == 0
    assert cols["region"]["stats"]["null_count"] > 0  # nulls untouched


@pytest.mark.parametrize(
    ("fixes", "code"),
    [
        ([], "no_fixes_selected"),
        ([{"id": "trim:nope"}], "unknown_fix"),
        ([{"id": "dedupe"}, {"id": "dedupe"}], "duplicate_fix"),
        ([{"id": "trim:region", "strategy": "keep"}], "invalid_strategy"),
        ([{"id": "nulls:cost", "strategy": "invent"}], "invalid_strategy"),
        ([{"id": "nulls:cost", "strategy": "keep"}], "nothing_to_apply"),
        ([{"id": "nulls:cost"}], "nothing_to_apply"),
    ],
)
def test_invalid_apply_requests(
    client: TestClient, owner: Account, messy: dict[str, Any], fixes: list[Any], code: str
) -> None:
    r = _apply(client, owner, messy, fixes)
    assert r.status_code == 422 and r.json()["code"] == code, r.text
    dataset = client.get(_url(messy, ""), headers=owner.headers).json()
    assert len(dataset["versions"]) == 1  # nothing was created


def test_apply_body_must_be_valid(
    client: TestClient, owner: Account, messy: dict[str, Any]
) -> None:
    r = client.post(_url(messy, "cleaning-plan/apply"), headers=owner.headers, json={})
    assert r.status_code == 422


def test_cleaning_failure_marks_only_the_new_version_failed(
    client: TestClient, owner: Account, messy: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("disk full /secret")

    monkeypatch.setattr("app.services.cleaning.apply_fixes", boom)
    detail = _apply(client, owner, messy, _fixes("trim:region")).json()
    v2, v1 = detail["versions"]
    assert v2["status"] == "failed" and "secret" not in v2["error"]
    assert detail["current_version_id"] == v1["id"] and detail["status"] == "ready"
    assert client.get(_url(messy, "preview"), headers=owner.headers).status_code == 200


def test_cleaning_enqueue_failure_marks_the_new_version_failed(
    client: TestClient, owner: Account, messy: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workers import tasks

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("redis is down")

    monkeypatch.setattr(tasks.apply_cleaning, "delay", broken)
    detail = _apply(client, owner, messy, _fixes("trim:region")).json()
    assert detail["versions"][0]["status"] == "failed"
    assert detail["current_version_id"] == detail["versions"][1]["id"]


def test_user_role_overrides_carry_over_to_the_cleaned_version(
    client: TestClient, owner: Account, messy: dict[str, Any]
) -> None:
    client.put(_url(messy, "columns/cost/role"), headers=owner.headers, json={"role": "discount"})
    _apply(client, owner, messy, _fixes("trim:region"))
    profile = client.get(_url(messy, "profile"), headers=owner.headers).json()
    assert profile["version"] == 2
    cost = next(c for c in profile["columns"] if c["name"] == "cost")
    assert (cost["semantic_role"], cost["role_source"]) == ("discount", "user")


def test_late_older_version_does_not_become_current(
    client: TestClient, db: Session, owner: Account, messy: dict[str, Any]
) -> None:
    """Two cleanings start from version 1; the older one finishing last must not win."""
    tenant = uuid.UUID(owner.tenant_id)
    user = _user(db, owner)
    dataset = db.get(Dataset, uuid.UUID(messy["id"]))
    source = db.get(DatasetVersion, uuid.UUID(messy["versions"][0]["id"]))
    assert dataset is not None and source is not None
    first, sel1 = cleaning.start_cleaning(db, user, dataset, source, [("trim:region", None)])
    second, sel2 = cleaning.start_cleaning(db, user, dataset, source, [("trim:product", None)])
    db.commit()
    assert (first.version, second.version) == (2, 3)

    cleaning.run_cleaning(tenant, second.id, sel2)  # newer finishes first
    db.expire_all()
    assert dataset.current_version_id == second.id

    cleaning.run_cleaning(tenant, first.id, sel1)  # older finishes late
    db.expire_all()
    assert dataset.current_version_id == second.id
    older = db.get(DatasetVersion, first.id)
    assert older is not None and older.status == "ready" and older.profile_status == "ready"


def test_cleaning_a_version_with_a_failed_profile_is_refused(
    client: TestClient, db: Session, owner: Account, messy: dict[str, Any]
) -> None:
    version = db.get(DatasetVersion, uuid.UUID(messy["versions"][0]["id"]))
    assert version is not None
    version.profile_status, version.profile_error = "failed", "boom"
    db.commit()
    r = _apply(client, owner, messy, _fixes("trim:region"))
    assert r.status_code == 409 and r.json()["code"] == "profile_failed"


def test_deleted_datasets_hide_the_profile_routes(
    client: TestClient, owner: Account, messy: dict[str, Any]
) -> None:
    assert client.delete(_url(messy, ""), headers=owner.headers).status_code == 204
    for tail in ("profile", "quality", "cleaning-plan"):
        assert client.get(_url(messy, tail), headers=owner.headers).status_code == 404
    assert client.post(_url(messy, "profile/rerun"), headers=owner.headers).status_code == 404


def test_profile_routes_require_authentication(client: TestClient, messy: dict[str, Any]) -> None:
    for method, tail in (
        ("get", "profile"),
        ("get", "quality"),
        ("get", "cleaning-plan"),
        ("post", "cleaning-plan/apply"),
        ("post", "profile/rerun"),
        ("put", "columns/cost/role"),
    ):
        assert getattr(client, method)(_url(messy, tail)).status_code == 401, tail


def test_new_routes_never_take_a_tenant_from_the_request(
    client: TestClient, owner: Account, messy: dict[str, Any]
) -> None:
    other = register(client, company="Other Corp")
    r = client.get(_url(messy, f"profile?tenant_id={other.tenant_id}"), headers=owner.headers)
    assert r.status_code == 200 and r.json()["version"] == 1  # extra query ignored
    assert client.get(_url(messy, "profile"), headers=other.headers).status_code == 404


def test_a_tiny_upload_gets_a_low_confidence_profile(client: TestClient, owner: Account) -> None:
    made = upload(client, owner, csv_bytes([[1, "a"], [2, "b"]], ["n", "label"]), "t.csv").json()
    quality = client.get(_url(made, "quality"), headers=owner.headers).json()
    assert quality["confidence"] == "low" and quality["warnings"]


def _user(db: Session, account: Account) -> Any:
    from app.models.user import User

    user = db.get(User, uuid.UUID(account.user_id))
    assert user is not None
    return user
