"""Helpers shared by API tests."""

import uuid
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

PASSWORD = "correct-horse-battery"
API = "/api/v1"


@dataclass
class Account:
    """A registered and logged-in user."""

    email: str
    token: str
    refresh: str
    user_id: str
    tenant_id: str

    @property
    def headers(self) -> dict[str, str]:
        """Authorization header for this account."""
        return {"Authorization": f"Bearer {self.token}"}


def refresh_cookie(response: Response) -> str:
    """Extract the raw refresh token from a Set-Cookie header."""
    cookie: SimpleCookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie["refresh_token"].value


def post_with_cookie(client: TestClient, path: str, raw: str | None) -> Response:
    """POST with an explicit refresh cookie, ignoring the client's cookie jar."""
    client.cookies.clear()
    headers = {"Cookie": f"refresh_token={raw}"} if raw else {}
    return client.post(f"{API}{path}", headers=headers)


def login(client: TestClient, email: str, password: str = PASSWORD) -> Response:
    """Call POST /auth/login."""
    client.cookies.clear()
    return client.post(f"{API}/auth/login", json={"email": email, "password": password})


def register(
    client: TestClient, email: str | None = None, company: str = "Acme", **extra: Any
) -> Account:
    """Register a tenant and log in as its owner."""
    email = email or f"owner-{uuid.uuid4().hex[:8]}@example.com"
    body = {
        "company_name": company,
        "email": email,
        "password": PASSWORD,
        "full_name": "Olivia Owner",
        **extra,
    }
    created = client.post(f"{API}/auth/register", json=body)
    assert created.status_code == 201, created.text
    return account_for(client, email, created.json()["user"]["id"], created.json()["tenant"]["id"])


def account_for(client: TestClient, email: str, user_id: str, tenant_id: str) -> Account:
    """Log in and wrap the result."""
    response = login(client, email)
    assert response.status_code == 200, response.text
    return Account(
        email=email,
        token=response.json()["access_token"],
        refresh=refresh_cookie(response),
        user_id=user_id,
        tenant_id=tenant_id,
    )


def add_member(
    client: TestClient, actor: Account, role: str = "member", email: str | None = None
) -> Account:
    """Create a team member through the API and log in as them."""
    email = email or f"{role}-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        f"{API}/team/members",
        headers=actor.headers,
        json={"email": email, "full_name": "Team Person", "password": PASSWORD, "role": role},
    )
    assert response.status_code == 201, response.text
    return account_for(client, email, response.json()["id"], actor.tenant_id)
