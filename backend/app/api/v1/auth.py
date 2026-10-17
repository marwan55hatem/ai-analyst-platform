"""Authentication endpoints: register, login, refresh, logout, me."""

from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response, status

from app.api import responses
from app.api.deps import CurrentUser, DbSession, Limiter, client_ip
from app.core.config import get_settings
from app.core.errors import UnauthorizedError
from app.models.user import User
from app.schemas.auth import LoginRequest, MeResponse, RegisterRequest, TokenResponse
from app.schemas.user import TenantOut, UserOut
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
COOKIE_PATH = "/api/v1/auth"


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        raw_token,
        max_age=settings.refresh_token_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=COOKIE_PATH,
    )


def _me(user: User) -> MeResponse:
    return MeResponse(
        user=UserOut.model_validate(user), tenant=TenantOut.model_validate(user.tenant)
    )


@router.post(
    "/register",
    response_model=MeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tenant and its owner",
    responses={**responses.CONFLICT, **responses.UNPROCESSABLE, **responses.RATE_LIMITED},
)
def register(
    body: RegisterRequest, request: Request, db: DbSession, limiter: Limiter
) -> MeResponse:
    """Register a company; the caller becomes its owner."""
    limiter.hit(f"register:{client_ip(request)}", 5, 3600)
    user = auth_service.register_tenant(
        db,
        company_name=body.company_name,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
    )
    return _me(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in and receive an access token",
    responses={**responses.UNAUTHORIZED, **responses.RATE_LIMITED},
)
def login(
    body: LoginRequest, request: Request, response: Response, db: DbSession, limiter: Limiter
) -> TokenResponse:
    """Return an access JWT and set the refresh token cookie."""
    ip = client_ip(request)
    limiter.hit(f"login:{ip}:{body.email}", 5, 60)
    limiter.hit(f"login-ip:{ip}", 30, 60)
    session = auth_service.login(db, email=body.email, password=body.password)
    _set_refresh_cookie(response, session.refresh_token)
    return TokenResponse(access_token=session.access_token, expires_in=session.expires_in)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate the refresh token",
    responses={**responses.UNAUTHORIZED, **responses.RATE_LIMITED},
)
def refresh(
    request: Request,
    response: Response,
    db: DbSession,
    limiter: Limiter,
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> TokenResponse:
    """Exchange the refresh cookie for a new access token and a rotated cookie."""
    limiter.hit(f"refresh:{client_ip(request)}", 30, 60)
    if not refresh_token:
        raise UnauthorizedError("Missing refresh token")
    session = auth_service.refresh(db, refresh_token)
    _set_refresh_cookie(response, session.refresh_token)
    return TokenResponse(access_token=session.access_token, expires_in=session.expires_in)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the refresh token",
)
def logout(
    response: Response,
    db: DbSession,
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> None:
    """Revoke the refresh token and clear the cookie."""
    auth_service.logout(db, refresh_token)
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Current user and tenant",
    responses=responses.UNAUTHORIZED,
)
def me(user: CurrentUser) -> MeResponse:
    """Return the authenticated user and their tenant."""
    return _me(user)
