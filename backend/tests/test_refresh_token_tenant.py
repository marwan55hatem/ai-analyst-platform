import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.auth import revoke_user_tokens
from tests.helpers import add_member, login, post_with_cookie, register


def test_refresh_token_tenant_always_matches_users_tenant(client: TestClient, db: Session) -> None:
    owner_a = register(client)
    owner_b = register(client)
    add_member(client, owner_a, "member")
    post_with_cookie(client, "/auth/refresh", owner_b.refresh)  # rotation creates a new row
    login(client, owner_a.email)
    rows = db.execute(select(RefreshToken, User).join(User, User.id == RefreshToken.user_id)).all()
    assert len(rows) >= 5
    assert all(token.tenant_id == user.tenant_id for token, user in rows)
    assert {str(t.tenant_id) for t, _ in rows} == {owner_a.tenant_id, owner_b.tenant_id}


def test_revoke_all_tokens_only_touches_that_tenants_rows(client: TestClient, db: Session) -> None:
    owner_a = register(client)
    owner_b = register(client)
    # A wrong tenant id must not revoke anything, even for an existing user id.
    revoke_user_tokens(db, uuid.UUID(owner_b.tenant_id), uuid.UUID(owner_a.user_id))
    db.commit()
    assert all(t.revoked_at is None for t in db.scalars(select(RefreshToken)))
    # The right tenant revokes only that user's tokens.
    revoke_user_tokens(db, uuid.UUID(owner_a.tenant_id), uuid.UUID(owner_a.user_id))
    db.commit()
    tokens = list(db.scalars(select(RefreshToken)))
    for token in tokens:
        expected_revoked = str(token.tenant_id) == owner_a.tenant_id
        assert (token.revoked_at is not None) == expected_revoked
    assert post_with_cookie(client, "/auth/refresh", owner_a.refresh).status_code == 401
    assert post_with_cookie(client, "/auth/refresh", owner_b.refresh).status_code == 200
