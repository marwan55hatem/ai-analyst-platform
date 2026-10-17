"""Registration, login, refresh-token rotation and logout."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError, UnauthorizedError
from app.core.password_policy import validate_password
from app.core.security import (
    create_access_token,
    dummy_hash,
    hash_password,
    hash_token,
    new_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.tenant import Tenant
from app.models.user import Role, User

logger = structlog.get_logger()


@dataclass(frozen=True)
class AuthSession:
    """Result of a login or refresh."""

    user: User
    access_token: str
    expires_in: int
    refresh_token: str


def _now() -> datetime:
    return datetime.now(UTC)


def register_tenant(
    db: Session, *, company_name: str, email: str, password: str, full_name: str
) -> User:
    """Create a tenant and its owner user."""
    validate_password(password, email=email, full_name=full_name)
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise ConflictError("Email is already registered", code="email_taken")
    tenant = Tenant(name=company_name)
    db.add(tenant)
    db.flush()
    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        role=Role.owner.value,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Email is already registered", code="email_taken") from exc
    logger.info("tenant_registered", tenant_id=str(tenant.id), user_id=str(user.id))
    return user


def _issue_session(db: Session, user: User) -> AuthSession:
    settings = get_settings()
    raw, token_hash = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=_now() + timedelta(days=settings.refresh_token_days),
        )
    )
    access, expires_in = create_access_token(user.id, user.tenant_id, user.role)
    db.commit()
    return AuthSession(user=user, access_token=access, expires_in=expires_in, refresh_token=raw)


def login(db: Session, *, email: str, password: str) -> AuthSession:
    """Verify credentials and start a session; failures are indistinguishable."""
    user = db.scalar(select(User).where(User.email == email))
    stored_hash = user.password_hash if user else dummy_hash()
    valid = verify_password(stored_hash, password)
    if user is None or not valid or not user.is_active:
        logger.info("login_failed")
        raise UnauthorizedError("Incorrect email or password")
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    logger.info("login_succeeded", tenant_id=str(user.tenant_id), user_id=str(user.id))
    return _issue_session(db, user)


def revoke_user_tokens(db: Session, user_id: uuid.UUID) -> None:
    """Revoke every active refresh token of a user (does not commit)."""
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


def refresh(db: Session, raw_token: str) -> AuthSession:
    """Rotate a refresh token; reuse of a revoked token revokes the whole family."""
    stmt = select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
    token = db.scalars(stmt.with_for_update()).first()
    if token is None:
        raise UnauthorizedError("Invalid refresh token")
    if token.revoked_at is not None:
        revoke_user_tokens(db, token.user_id)
        db.commit()
        logger.warning("refresh_token_reuse_detected", user_id=str(token.user_id))
        raise UnauthorizedError("Invalid refresh token")
    if token.expires_at <= _now():
        raise UnauthorizedError("Refresh token expired")
    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("Invalid refresh token")
    token.revoked_at = _now()
    return _issue_session(db, user)


def logout(db: Session, raw_token: str | None) -> None:
    """Revoke the presented refresh token (idempotent)."""
    if not raw_token:
        return
    token = db.scalars(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
    ).first()
    if token is not None and token.revoked_at is None:
        token.revoked_at = _now()
        db.commit()
