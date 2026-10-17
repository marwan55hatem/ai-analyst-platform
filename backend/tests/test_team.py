from fastapi.testclient import TestClient

from tests.helpers import API, PASSWORD, add_member, login, post_with_cookie, register


def _role(client: TestClient, actor_headers: dict[str, str], member_id: str, role: str) -> int:
    return client.put(
        f"{API}/team/members/{member_id}/role", headers=actor_headers, json={"role": role}
    ).status_code


def test_owner_lists_and_creates_members(client: TestClient) -> None:
    owner = register(client)
    member = add_member(client, owner, "member")
    response = client.get(f"{API}/team/members", headers=owner.headers)
    assert response.status_code == 200
    emails = {m["email"] for m in response.json()}
    assert emails == {owner.email, member.email}
    assert all("password" not in m and "tenant_id" not in m for m in response.json())


def test_members_cannot_use_team_endpoints(client: TestClient) -> None:
    owner = register(client)
    member = add_member(client, owner, "member")
    assert client.get(f"{API}/team/members", headers=member.headers).status_code == 403
    assert client.get(f"{API}/team/members").status_code == 401
    forbidden = client.post(
        f"{API}/team/members",
        headers=member.headers,
        json={"email": "n@example.com", "full_name": "N N", "password": PASSWORD},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "forbidden"


def test_create_member_validation(client: TestClient) -> None:
    owner = register(client)
    body = {"email": "new@example.com", "full_name": "New Person", "password": PASSWORD}
    assert client.post(f"{API}/team/members", headers=owner.headers, json=body).status_code == 201
    duplicate = client.post(f"{API}/team/members", headers=owner.headers, json=body)
    assert duplicate.status_code == 409
    weak = client.post(
        f"{API}/team/members",
        headers=owner.headers,
        json={**body, "email": "w@example.com", "password": "short"},
    )
    assert weak.status_code == 422
    assert weak.json()["code"] == "weak_password"
    with_tenant = client.post(
        f"{API}/team/members",
        headers=owner.headers,
        json={**body, "email": "t@example.com", "tenant_id": owner.tenant_id},
    )
    assert with_tenant.status_code == 422


def test_admin_permissions(client: TestClient) -> None:
    owner = register(client)
    admin = add_member(client, owner, "admin")
    member = add_member(client, admin, "member")
    assert _role(client, admin.headers, member.user_id, "admin") == 200
    # Admins cannot create owners, grant ownership, or touch owners.
    create_owner = client.post(
        f"{API}/team/members",
        headers=admin.headers,
        json={
            "email": "o2@example.com",
            "full_name": "O Two",
            "password": PASSWORD,
            "role": "owner",
        },
    )
    assert create_owner.status_code == 403
    assert _role(client, admin.headers, member.user_id, "owner") == 403
    assert _role(client, admin.headers, owner.user_id, "member") == 403
    deactivate_owner = client.post(
        f"{API}/team/members/{owner.user_id}/deactivate", headers=admin.headers
    )
    assert deactivate_owner.status_code == 403
    assert deactivate_owner.json()["code"] == "owner_protected"


def test_tenant_always_keeps_one_owner(client: TestClient) -> None:
    owner = register(client)
    # Sole owner cannot demote or deactivate themselves.
    demote = client.put(
        f"{API}/team/members/{owner.user_id}/role", headers=owner.headers, json={"role": "admin"}
    )
    assert demote.status_code == 409
    assert demote.json()["code"] == "last_owner"
    own_deactivate = client.post(
        f"{API}/team/members/{owner.user_id}/deactivate", headers=owner.headers
    )
    assert own_deactivate.status_code == 409
    # With a second owner the first can step down, and then the last one is protected.
    second = add_member(client, owner, "owner")
    assert _role(client, owner.headers, owner.user_id, "member") == 200
    assert _role(client, second.headers, second.user_id, "admin") == 409
    assert (
        client.post(
            f"{API}/team/members/{second.user_id}/deactivate", headers=second.headers
        ).status_code
        == 409
    )


def test_role_change_applies_immediately_despite_old_token(client: TestClient) -> None:
    owner = register(client)
    admin = add_member(client, owner, "admin")
    assert client.get(f"{API}/team/members", headers=admin.headers).status_code == 200
    assert _role(client, owner.headers, admin.user_id, "member") == 200
    assert client.get(f"{API}/team/members", headers=admin.headers).status_code == 403


def test_deactivated_member_loses_access(client: TestClient) -> None:
    owner = register(client)
    member = add_member(client, owner, "member")
    response = client.post(f"{API}/team/members/{member.user_id}/deactivate", headers=owner.headers)
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert login(client, member.email).status_code == 401
    assert client.get(f"{API}/auth/me", headers=member.headers).status_code == 401
    assert post_with_cookie(client, "/auth/refresh", member.refresh).status_code == 401
