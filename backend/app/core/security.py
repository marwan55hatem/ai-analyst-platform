"""Password hashing, JWT access tokens and refresh token helpers."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings
from app.core.errors import UnauthorizedError

REQUIRED_CLAIMS = ["sub", "tenant_id", "role", "exp", "iat", "jti"]


@lru_cache
def _hasher() -> PasswordHasher:
    return PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password with argon2id."""
    return _hasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Check a password against its hash without raising."""
    try:
        return _hasher().verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """Return True when the hash uses outdated argon2 parameters."""
    return _hasher().check_needs_rehash(password_hash)


@lru_cache
def dummy_hash() -> str:
    """Return a valid hash used to equalise timing when the account does not exist."""
    return _hasher().hash("not-a-real-password")


@dataclass(frozen=True)
class TokenClaims:
    """Validated access token claims."""

    sub: uuid.UUID
    tenant_id: uuid.UUID
    role: str
    jti: str


def create_access_token(user_id: uuid.UUID, tenant_id: uuid.UUID, role: str) -> tuple[str, int]:
    """Return (jwt, expires_in_seconds) for a user."""
    settings = get_settings()
    now = datetime.now(UTC)
    lifetime = timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "iat": now,
        "exp": now + lifetime,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, int(lifetime.total_seconds())


def decode_access_token(token: str) -> TokenClaims:
    """Validate signature, expiry and required claims; raise 401 otherwise."""
    settings = get_settings()
    try:
        data = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": REQUIRED_CLAIMS},
        )
        return TokenClaims(
            sub=uuid.UUID(data["sub"]),
            tenant_id=uuid.UUID(data["tenant_id"]),
            role=str(data["role"]),
            jti=str(data["jti"]),
        )
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise UnauthorizedError("Invalid or expired token") from exc


def new_refresh_token() -> tuple[str, str]:
    """Return (raw_token, sha256_hash); only the hash is stored."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    """Return the SHA-256 hex digest of a refresh token."""
    return hashlib.sha256(raw.encode()).hexdigest()
