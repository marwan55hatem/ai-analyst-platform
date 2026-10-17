from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.refresh_token import RefreshToken
from tests.helpers import (
    API,
    PASSWORD,
    login,
    post_with_cookie,
    refresh_cookie,
    register,
)


def test_register_creates_tenant_and_owner(client: TestClient) -> None:
    response = client.post(
        f"{API}/auth/register",
        json={
            "company_name": "Acme",
            "email": "Owner@Example.COM",
            "password": PASSWORD,
            "full_name": "Olivia",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "owner@example.com"
    assert body["user"]["role"] == "owner"
    assert body["tenant"]["name"] == "Acme"
    assert "password" not in response.text


def test_register_duplicate_email_is_case_insensitive(client: TestClient) -> None:
    register(client, "dup@example.com")
    response = client.post(
        f"{API}/auth/register",
        json={
            "company_name": "Other",
            "email": "DUP@example.com",
            "password": PASSWORD,
            "full_name": "Someone",
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "email_taken"


def test_register_rejects_client_supplied_tenant_id(client: TestClient) -> None:
    response = client.post(
        f"{API}/auth/register",
        json={
            "company_name": "Acme",
            "email": "x@example.com",
            "password": PASSWORD,
            "full_name": "X Person",
            "tenant_id": "00000000-0000-0000-0000-000000000001",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


@pytest.mark.parametrize(
    "password",
    ["short", "password123", "aaaaaaaaaaaa", "jamie.smith.2024", "Olivia Owner!"],
)
def test_register_enforces_password_policy(client: TestClient, password: str) -> None:
    response = client.post(
        f"{API}/auth/register",
        json={
            "company_name": "Acme",
            "email": "jamie.smith@example.com",
            "password": password,
            "full_name": "Olivia Owner",
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "weak_password"
    assert body["details"]


def test_login_returns_jwt_and_secure_refresh_cookie(client: TestClient) -> None:
    account = register(client, "login@example.com")
    response = login(client, "login@example.com")
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 30 * 60
    claims = jwt.decode(body["access_token"], options={"verify_signature": False})
    assert {"sub", "tenant_id", "role", "exp", "iat", "jti"} <= claims.keys()
    assert claims["sub"] == account.user_id
    assert claims["tenant_id"] == account.tenant_id
    assert claims["role"] == "owner"
    cookie = response.headers["set-cookie"].lower()
    for flag in ("httponly", "secure", "samesite=lax", "path=/api/v1/auth"):
        assert flag in cookie
    assert "refresh_token" not in body


def test_wrong_password_and_unknown_email_are_indistinguishable(client: TestClient) -> None:
    register(client, "real@example.com")
    wrong = login(client, "real@example.com", "not-the-password")
    unknown = login(client, "ghost@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["code"] == "unauthorized"


def test_me_requires_token_and_returns_user_with_tenant(client: TestClient) -> None:
    anonymous = client.get(f"{API}/auth/me")
    assert anonymous.status_code == 401
    assert set(anonymous.json()) == {"code", "message", "details"}
    account = register(client, "me@example.com", company="Me Corp")
    me = client.get(f"{API}/auth/me", headers=account.headers)
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "me@example.com"
    assert me.json()["tenant"]["name"] == "Me Corp"


def test_refresh_rotates_token(client: TestClient) -> None:
    account = register(client)
    response = post_with_cookie(client, "/auth/refresh", account.refresh)
    assert response.status_code == 200
    new_refresh = refresh_cookie(response)
    assert new_refresh != account.refresh
    me = client.get(
        f"{API}/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"}
    )
    assert me.status_code == 200
    # The old token is revoked.
    assert post_with_cookie(client, "/auth/refresh", account.refresh).status_code == 401


def test_reusing_rotated_refresh_token_revokes_the_whole_family(client: TestClient) -> None:
    account = register(client)
    rotated = post_with_cookie(client, "/auth/refresh", account.refresh)
    newest = refresh_cookie(rotated)
    assert post_with_cookie(client, "/auth/refresh", account.refresh).status_code == 401
    assert post_with_cookie(client, "/auth/refresh", newest).status_code == 401


def test_refresh_without_or_with_bad_cookie_is_401(client: TestClient) -> None:
    assert post_with_cookie(client, "/auth/refresh", None).status_code == 401
    assert post_with_cookie(client, "/auth/refresh", "garbage").status_code == 401


def test_expired_refresh_token_is_401(client: TestClient, db: Session) -> None:
    account = register(client)
    db.execute(update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    db.commit()
    assert post_with_cookie(client, "/auth/refresh", account.refresh).status_code == 401


def test_logout_revokes_refresh_token_and_is_idempotent(client: TestClient) -> None:
    account = register(client)
    response = post_with_cookie(client, "/auth/logout", account.refresh)
    assert response.status_code == 204
    assert post_with_cookie(client, "/auth/refresh", account.refresh).status_code == 401
    assert post_with_cookie(client, "/auth/logout", account.refresh).status_code == 204
    assert post_with_cookie(client, "/auth/logout", None).status_code == 204
