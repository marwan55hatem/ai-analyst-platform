"""Tenant-scoped repositories: every query is filtered by tenant_id automatically."""

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.base import TenantMixin
from app.models.user import Role, User


class TenantRepository[ModelT: TenantMixin]:
    """Base class for access to tenant-owned rows.

    The tenant id is fixed at construction (from the JWT) and applied to every
    query, so route code cannot forget the filter or pass another tenant's id.
    """

    model: type[ModelT]

    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        """Bind the repository to one session and one tenant."""
        self.session = session
        self.tenant_id = tenant_id

    def _select(self) -> Select[Any]:
        return select(self.model).where(self.model.tenant_id == self.tenant_id)

    def get(self, object_id: uuid.UUID, *, for_update: bool = False) -> ModelT | None:
        """Return the row with this id in the current tenant, or None."""
        stmt = self._select().where(self.model.id == object_id)  # type: ignore[attr-defined]
        if for_update:
            stmt = stmt.with_for_update()
        return self.session.scalars(stmt).first()

    def get_or_404(self, object_id: uuid.UUID, *, for_update: bool = False) -> ModelT:
        """Return the row or raise 404 (also for rows owned by other tenants)."""
        obj = self.get(object_id, for_update=for_update)
        if obj is None:
            raise NotFoundError(f"{self.model.__name__} not found")
        return obj

    def list_all(self, *, limit: int = 100, offset: int = 0) -> list[ModelT]:
        """Return a page of rows of the current tenant."""
        stmt = self._select().limit(limit).offset(offset)
        return list(self.session.scalars(stmt))

    def add(self, **fields: object) -> ModelT:
        """Create a row in the current tenant; a client-supplied tenant_id is refused."""
        if "tenant_id" in fields:
            raise ValueError("tenant_id is set by the repository")
        obj = self.model(**fields, tenant_id=self.tenant_id)  # type: ignore[call-arg]
        self.session.add(obj)
        return obj

    def delete(self, obj: ModelT) -> None:
        """Delete a row previously fetched through this repository."""
        self.session.delete(obj)


class UserRepository(TenantRepository[User]):
    """Users of one tenant."""

    model = User

    def list_members(self) -> list[User]:
        """Return all users of the tenant ordered by creation time."""
        return list(self.session.scalars(self._select().order_by(User.created_at, User.email)))

    def active_owners(self, *, lock: bool = False) -> list[User]:
        """Return active owners; with lock=True rows are locked until commit."""
        stmt = self._select().where(User.role == Role.owner.value, User.is_active.is_(True))
        if lock:
            stmt = stmt.with_for_update()
        return list(self.session.scalars(stmt))
