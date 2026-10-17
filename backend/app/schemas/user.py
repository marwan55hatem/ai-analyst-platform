"""User and tenant response schemas."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.models.user import Role

Email = Annotated[EmailStr, AfterValidator(lambda v: v.lower())]
Password = Annotated[str, Field(min_length=1, max_length=128)]
FullName = Annotated[str, Field(min_length=1, max_length=200)]


class StrictModel(BaseModel):
    """Request base model: unknown fields (such as tenant_id) are rejected."""

    model_config = ConfigDict(extra="forbid")


class TenantOut(BaseModel):
    """Tenant as returned to its own members."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime


class UserOut(BaseModel):
    """User as returned to members of the same tenant."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime
