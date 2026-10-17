"""Tenant isolation: tenant B can never see or change tenant A's data."""

import re
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.repository import UserRepository
from app.main import app
from app.models.user import Role
from tests.helpers import API, add_member, register

# Tenant-scoped routes that take a resource id. A new such route must be added here
# (test_every_resource_route_is_covered fails otherwise).
RESOURCE_ROUTES = {
    ("PUT", "/api/v1/team/members/{member_id}/role"),
    ("POST", "/api/v1/team/members/{member_id}/deactivate"),
}


def _call(client: TestClient, method: str, path: str, headers: dict[str, str]) -> int:
    body = {"role": "member"} if path.endswith("/role") else None
    return client.request(method, path, headers=headers, json=body).status_code


def test_tenant_b_gets_404_for_every_tenant_a_resource(client: TestClient) -> None:
    owner_a = register(client, company="A Corp")
    owner_b = register(client, company="B Corp")
    member_a = add_member(client, owner_a, "member")
    admin_a = add_member(client, owner_a, "admin")

    for method, template in sorted(RESOURCE_ROUTES):
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
                schema = body["content"]["application/json"]["schema"]
                assert "tenant_id" not in _walk(schema, components), (method, path)
