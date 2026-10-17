"""User model and roles."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantMixin, created_at_column
from app.models.tenant import Tenant


class Role(StrEnum):
    """Tenant-level roles, most to least privileged."""

    owner = "owner"
    admin = "admin"
    member = "member"


class User(TenantMixin, Base):
    """A person belonging to exactly one tenant."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role in ('owner', 'admin', 'member')", name="role"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default=Role.member.value)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    created_at: Mapped[datetime] = created_at_column()

    tenant: Mapped[Tenant] = relationship()
