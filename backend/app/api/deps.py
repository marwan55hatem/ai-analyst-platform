"""Shared FastAPI dependencies: DB session, authentication, roles, rate limiting."""

from collections.abc import Callable
from typing import Annotated

import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.rate_limit import RateLimiter, get_rate_limiter
from app.core.security import decode_access_token
from app.db.repository import UserRepository
from app.db.session import get_db
from app.models.user import Role, User

DbSession = Annotated[Session, Depends(get_db)]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
_bearer = HTTPBearer(auto_error=False, description="Access token from /auth/login")


def _load_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Resolve the user from the JWT; tenant_id comes from the token only."""
    if credentials is None:
        raise UnauthorizedError()
    claims = decode_access_token(credentials.credentials)
    user = UserRepository(db, claims.tenant_id).get(claims.sub)
    if user is None or not user.is_active:
        raise UnauthorizedError("Invalid or expired token")
    return user


async def get_current_user(request: Request, user: Annotated[User, Depends(_load_user)]) -> User:
    """Return the authenticated user and bind tenant/user ids to every log line."""
    structlog.contextvars.bind_contextvars(tenant_id=str(user.tenant_id), user_id=str(user.id))
    request.state.tenant_id = str(user.tenant_id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_user_repo(user: CurrentUser, db: DbSession) -> UserRepository:
    """Return a user repository bound to the caller's tenant."""
    return UserRepository(db, user.tenant_id)


UserRepo = Annotated[UserRepository, Depends(get_user_repo)]


def require_role(*roles: Role) -> Callable[[User], User]:
    """Build a dependency that allows only the given roles (403 otherwise)."""

    def checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise ForbiddenError("Insufficient role for this action")
        return user

    return checker


def client_ip(request: Request) -> str:
    """Return the direct peer address (proxy headers are not trusted)."""
    return request.client.host if request.client else "unknown"
