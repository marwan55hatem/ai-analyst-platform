"""Tenant isolation: tenant B can never see or change tenant A's data."""

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.repository import UserRepository
from app.main import app
from app.models.user import Role
from tests.dataset_helpers import sample, upload_ready
from tests.ground_truth_fixtures import analyze, make
from tests.helpers import API, add_member, register

# Tenant-scoped routes that take a resource id. A new such route must be added here
# (test_every_resource_route_is_covered fails otherwise).
TEAM_ROUTES = {
    ("PUT", "/api/v1/team/members/{member_id}/role"),
    ("POST", "/api/v1/team/members/{member_id}/deactivate"),
}
DATASET_ROUTES = {
    ("GET", "/api/v1/datasets/{dataset_id}"),
    ("DELETE", "/api/v1/datasets/{dataset_id}"),
    ("POST", "/api/v1/datasets/{dataset_id}/versions"),
    ("GET", "/api/v1/datasets/{dataset_id}/sheets"),
    ("GET", "/api/v1/datasets/{dataset_id}/preview"),
    ("GET", "/api/v1/datasets/{dataset_id}/profile"),
    ("GET", "/api/v1/datasets/{dataset_id}/quality"),
    ("GET", "/api/v1/datasets/{dataset_id}/cleaning-plan"),
    ("POST", "/api/v1/datasets/{dataset_id}/cleaning-plan/apply"),
    ("POST", "/api/v1/datasets/{dataset_id}/profile/rerun"),
    ("PUT", "/api/v1/datasets/{dataset_id}/columns/{name}/role"),
}
ANALYSIS_ROUTES = {
    ("GET", "/api/v1/datasets/{dataset_id}/capabilities"),
    ("POST", "/api/v1/datasets/{dataset_id}/analyze"),
    ("GET", "/api/v1/datasets/{dataset_id}/analysis-runs"),
    ("GET", "/api/v1/analysis-runs/{run_id}"),
    ("GET", "/api/v1/analysis-runs/{run_id}/insights"),
    ("GET", "/api/v1/analysis-runs/{run_id}/anomalies"),
    ("GET", "/api/v1/analysis-runs/{run_id}/segments"),
    ("GET", "/api/v1/analysis-runs/{run_id}/churn"),
    ("GET", "/api/v1/analysis-runs/{run_id}/drivers"),
    ("POST", "/api/v1/datasets/{dataset_id}/what-if"),
    ("GET", "/api/v1/insights/{insight_id}"),
}
FORECAST_ROUTES = {
    ("POST", "/api/v1/datasets/{dataset_id}/forecast"),
    ("GET", "/api/v1/analysis-runs/{run_id}/forecast"),
    ("GET", "/api/v1/forecast-runs/{forecast_run_id}"),
}
RESOURCE_ROUTES = TEAM_ROUTES | DATASET_ROUTES | ANALYSIS_ROUTES | FORECAST_ROUTES
# Routes that work without a token; every other route must answer 401 anonymously.
PUBLIC_ROUTES = {
    ("GET", "/api/v1/health"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
}


def _call(client: TestClient, method: str, path: str, headers: dict[str, str]) -> int:
    if "/columns/" in path:
        body = {"role": "revenue"}
        return client.request(method, path, headers=headers, json=body).status_code
    if method == "POST" and path.endswith("/what-if"):
        body = {"scenarios": [{"lever": "volume", "change_pct": 5}]}
        return client.request(method, path, headers=headers, json=body).status_code
    if method == "POST" and path.endswith("/forecast"):
        return client.request(method, path, headers=headers, json={}).status_code
    if path.endswith("/cleaning-plan/apply"):
        body = {"fixes": [{"id": "trim:region"}]}
        return client.request(method, path, headers=headers, json=body).status_code
    if path.endswith("/role"):
        return client.request(method, path, headers=headers, json={"role": "member"}).status_code
    if path.endswith("/versions"):
        files = {"file": ("x.csv", b"a,b\n1,2\n")}
        return client.request(method, path, headers=headers, files=files).status_code
    return client.request(method, path, headers=headers).status_code


def test_tenant_b_gets_404_for_every_tenant_a_resource(client: TestClient) -> None:
    owner_a = register(client, company="A Corp")
    owner_b = register(client, company="B Corp")
    member_a = add_member(client, owner_a, "member")
    admin_a = add_member(client, owner_a, "admin")

    for method, template in sorted(TEAM_ROUTES):
        for victim in (owner_a, member_a, admin_a):
            path = template.replace("{member_id}", victim.user_id)
            assert _call(client, method, path, owner_b.headers) == 404, (method, path)

    # Nothing changed for tenant A.
    members = client.get(f"{API}/team/members", headers=owner_a.headers).json()
    assert {m["email"]: (m["role"], m["is_active"]) for m in members} == {
        owner_a.email: ("owner", True),
        member_a.email: ("member", True),
        admin_a.email: ("admin", True),
    }


def test_tenant_b_gets_404_for_every_tenant_a_dataset_route(client: TestClient) -> None:
    owner_a = register(client, company="A Corp")
    owner_b = register(client, company="B Corp")
    admin_b = add_member(client, owner_b, "admin")
    made = upload_ready(client, owner_a, sample("retail_two_sheets.xlsx"), "book.xlsx")
    dataset_id = made["id"]

    for method, template in sorted(DATASET_ROUTES):
        path = template.replace("{dataset_id}", dataset_id).replace("{name}", "region")
        for attacker in (owner_b, admin_b):
            assert _call(client, method, path, attacker.headers) == 404, (method, path)

    # Tenant A's dataset is untouched and tenant B sees nothing.
    detail = client.get(f"{API}/datasets/{dataset_id}", headers=owner_a.headers).json()
    assert detail["status"] == "ready" and len(detail["versions"]) == 1
    assert client.get(f"{API}/datasets", headers=owner_b.headers).json() == []
    assert len(client.get(f"{API}/datasets", headers=owner_a.headers).json()) == 1


@pytest.mark.slow
def test_tenant_b_gets_404_for_every_tenant_a_analysis_route(
    client: TestClient, tmp_path: Path
) -> None:
    owner_a = register(client, company="A Corp")
    owner_b = register(client, company="B Corp")
    admin_b = add_member(client, owner_b, "admin")
    content, _ = make(tmp_path)
    dataset_id, run, insights = analyze(client, owner_a, content)
    assert insights
    ids = {
        "{dataset_id}": dataset_id,
        "{run_id}": run["id"],
        "{insight_id}": insights[0]["id"],
    }

    for method, template in sorted(ANALYSIS_ROUTES):
        path = template
        for key, value in ids.items():
            path = path.replace(key, value)
        for attacker in (owner_b, admin_b):
            assert _call(client, method, path, attacker.headers) == 404, (method, path)
        assert _call(client, method, path, owner_a.headers) in {200, 202}, (method, path)

    # Tenant B ran nothing, so its own queries stay empty and tenant A's run is unchanged.
    assert (
        client.get(f"{API}/analysis-runs/{run['id']}", headers=owner_a.headers).json()["status"]
        == "completed"
    )


def test_tenant_b_gets_404_for_every_tenant_a_forecast_route(
    client: TestClient, tmp_path: Path
) -> None:
    owner_a = register(client, company="A Corp")
    owner_b = register(client, company="B Corp")
    admin_b = add_member(client, owner_b, "admin")
    content, _ = make(tmp_path)
    dataset_id, run, _ = analyze(client, owner_a, content)
    queued = client.post(f"{API}/datasets/{dataset_id}/forecast", headers=owner_a.headers, json={})
    assert queued.status_code == 202, queued.text
    ids = {
        "{dataset_id}": dataset_id,
        "{run_id}": run["id"],
        "{forecast_run_id}": queued.json()["id"],
    }

    for method, template in sorted(FORECAST_ROUTES):
        path = template
        for key, value in ids.items():
            path = path.replace(key, value)
        for attacker in (owner_b, admin_b):
            assert _call(client, method, path, attacker.headers) == 404, (method, path)
        assert _call(client, method, path, owner_a.headers) in {200, 202}, (method, path)

    # Tenant B cannot see tenant A's forecast, and tenant A's result is untouched.
    own = client.get(f"{API}/forecast-runs/{queued.json()['id']}", headers=owner_a.headers).json()
    assert own["status"] == "completed" and own["result"]["insights"]


def test_query_layer_refuses_other_tenants_version(client: TestClient, db: Session) -> None:
    import uuid

    from app.core.errors import NotFoundError
    from app.services.query import SafeQueryService

    owner_a = register(client, company="A Corp")
    owner_b = register(client, company="B Corp")
    made = upload_ready(client, owner_a, sample("retail_clean.csv"), "a.csv")
    version_id = uuid.UUID(made["versions"][0]["id"])
    SafeQueryService(db).run(uuid.UUID(owner_a.tenant_id), version_id, "SELECT 1")
    try:
        SafeQueryService(db).run(uuid.UUID(owner_b.tenant_id), version_id, "SELECT 1")
    except NotFoundError:
        return
    raise AssertionError("tenant B must not reach tenant A's version")


def test_every_non_public_route_requires_authentication(client: TestClient) -> None:
    for path, operations in app.openapi()["paths"].items():
        concrete = re.sub(r"\{\w+\}", "00000000-0000-0000-0000-000000000000", path)
        for method in operations:
            if (method.upper(), path) in PUBLIC_ROUTES:
                continue
            assert client.request(method, concrete).status_code == 401, (method, path)


def test_lists_only_contain_own_tenant(client: TestClient) -> None:
    owner_a = register(client)
    owner_b = register(client)
    add_member(client, owner_a, "member")
    b_members = client.get(f"{API}/team/members", headers=owner_b.headers).json()
    assert [m["email"] for m in b_members] == [owner_b.email]


def test_every_resource_route_is_covered() -> None:
    found = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if re.search(r"\{\w+\}", path)
        for method in operations
    }
    assert found == RESOURCE_ROUTES


def test_repository_filters_by_tenant(client: TestClient, db: Session) -> None:
    a = register(client)
    b = register(client)
    import uuid

    repo_b = UserRepository(db, uuid.UUID(b.tenant_id))
    assert repo_b.get(uuid.UUID(a.user_id)) is None
    assert [u.email for u in repo_b.list_all()] == [b.email]
    assert [u.email for u in repo_b.active_owners()] == [b.email]
    created = repo_b.add(
        email="repo@example.com", full_name="Repo User", password_hash="x", role=Role.member.value
    )
    assert str(created.tenant_id) == b.tenant_id


def test_repository_refuses_client_tenant_id(db: Session) -> None:
    import uuid

    repo = UserRepository(db, uuid.uuid4())
    try:
        repo.add(tenant_id=uuid.uuid4(), email="x@example.com")
    except ValueError:
        return
    raise AssertionError("tenant_id should be refused")


def _walk(schema: Any, components: dict[str, Any], seen: set[str] | None = None) -> set[str]:
    """Collect every property name reachable from a schema."""
    seen = seen or set()
    names: set[str] = set()
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if ref and ref not in seen:
            seen.add(ref)
            names |= _walk(components[ref.rsplit("/", 1)[-1]], components, seen)
        for name, sub in schema.get("properties", {}).items():
            names.add(name)
            names |= _walk(sub, components, seen)
        for key in ("items", "anyOf", "allOf", "oneOf"):
            value = schema.get(key)
            for item in value if isinstance(value, list) else [value]:
                if item:
                    names |= _walk(item, components, seen)
    return names


def test_no_endpoint_accepts_tenant_id_from_the_client() -> None:
    spec = app.openapi()
    components = spec["components"]["schemas"]
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            for param in operation.get("parameters", []):
                assert "tenant" not in param["name"].lower(), (method, path)
            body = operation.get("requestBody")
            if body:
                for media in body["content"].values():  # json and multipart/form-data
                    assert "tenant_id" not in _walk(media["schema"], components), (method, path)
