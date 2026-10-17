"""Auth request/response schemas."""

from typing import Annotated

from pydantic import BaseModel, Field

from app.schemas.user import Email, FullName, Password, StrictModel, TenantOut, UserOut


class RegisterRequest(StrictModel):
    """Create a tenant and its owner."""

    company_name: Annotated[str, Field(min_length=1, max_length=200)]
    email: Email
    password: Password
    full_name: FullName


class LoginRequest(StrictModel):
    """Email + password login."""

    email: Email
    password: Password


class TokenResponse(BaseModel):
    """Short-lived access token; the refresh token travels in an httpOnly cookie."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    """Current user with their tenant."""

    user: UserOut
    tenant: TenantOut
