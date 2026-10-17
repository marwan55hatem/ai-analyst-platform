import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    password_needs_rehash,
    verify_password,
)
from tests.helpers import API, register


def _claims(user_id: str, tenant_id: str, **override: object) -> dict[str, object]:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": "owner",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "jti": uuid.uuid4().hex,
    }
    claims.update(override)
    return claims


def _me(client: TestClient, token: str) -> int:
    return client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code


def test_valid_forged_with_secret_works_but_variants_do_not(client: TestClient) -> None:
    account = register(client)
    secret = get_settings().jwt_secret
    good = jwt.encode(_claims(account.user_id, account.tenant_id), secret, algorithm="HS256")
    assert _me(client, good) == 200

    expired = jwt.encode(
        _claims(
            account.user_id,
            account.tenant_id,
            exp=datetime.now(UTC) - timedelta(seconds=5),
        ),
        secret,
        algorithm="HS256",
    )
    assert _me(client, expired) == 401

    wrong_secret = jwt.encode(
        _claims(account.user_id, account.tenant_id), "x" * 40, algorithm="HS256"
    )
    assert _me(client, wrong_secret) == 401

    none_alg = jwt.encode(_claims(account.user_id, account.tenant_id), None, algorithm="none")
    assert _me(client, none_alg) == 401

    tampered = good[:-3] + ("AAA" if not good.endswith("AAA") else "BBB")
    assert _me(client, tampered) == 401

    missing_claim = jwt.encode(
        {k: v for k, v in _claims(account.user_id, account.tenant_id).items() if k != "tenant_id"},
        secret,
        algorithm="HS256",
    )
    assert _me(client, missing_claim) == 401
    assert _me(client, "not-a-jwt") == 401


def test_token_with_another_tenants_id_does_not_resolve_a_user(client: TestClient) -> None:
    a = register(client)
    b = register(client)
    forged = jwt.encode(
        _claims(a.user_id, b.tenant_id), get_settings().jwt_secret, algorithm="HS256"
    )
    assert _me(client, forged) == 401


def test_password_hashing_roundtrip() -> None:
    hashed = hash_password("a-long-enough-password")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "a-long-enough-password")
    assert not verify_password(hashed, "wrong")
    assert not verify_password("not-a-hash", "wrong")
    assert not password_needs_rehash(hashed)


def test_refresh_token_helpers() -> None:
    raw, hashed = new_refresh_token()
    assert hashed == hash_token(raw)
    assert raw not in hashed
    assert new_refresh_token()[0] != raw


def test_access_token_lifetime_comes_from_settings() -> None:
    token, expires_in = create_access_token(uuid.uuid4(), uuid.uuid4(), "member")
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == expires_in == get_settings().access_token_minutes * 60
