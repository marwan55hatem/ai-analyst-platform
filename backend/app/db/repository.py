"""Tenant-scoped repositories: every query is filtered by tenant_id automatically."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.base import TenantMixin
from app.models.dataset import Dataset, DatasetVersion
from app.models.profile import CleaningLog, ColumnProfile
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


class DatasetRepository(TenantRepository[Dataset]):
    """Datasets of one tenant; soft-deleted rows are invisible unless asked for."""

    model = Dataset

    def _select(self) -> Select[Any]:
        return super()._select().where(Dataset.deleted_at.is_(None))

    def get_including_deleted(self, dataset_id: uuid.UUID) -> Dataset | None:
        """Return a dataset of this tenant even if it was soft-deleted."""
        stmt = select(Dataset).where(Dataset.tenant_id == self.tenant_id, Dataset.id == dataset_id)
        return self.session.scalars(stmt).first()

    def list_recent(self, *, limit: int = 100, offset: int = 0) -> list[Dataset]:
        """Return the newest datasets first."""
        stmt = self._select().order_by(Dataset.created_at.desc(), Dataset.id).limit(limit)
        return list(self.session.scalars(stmt.offset(offset)))

    def soft_delete(self, dataset: Dataset) -> None:
        """Mark a dataset deleted (data files are kept)."""
        dataset.deleted_at = datetime.now(UTC)


class ColumnProfileRepository(TenantRepository[ColumnProfile]):
    """Column profiles of one tenant."""

    model = ColumnProfile

    def for_version(self, version_id: uuid.UUID) -> list[ColumnProfile]:
        """Return the profiles of a version in a stable order."""
        stmt = (
            self._select()
            .where(ColumnProfile.dataset_version_id == version_id)
            .order_by(ColumnProfile.name)
        )
        return list(self.session.scalars(stmt))

    def get_by_name(self, version_id: uuid.UUID, name: str) -> ColumnProfile | None:
        """Return one column of a version, or None."""
        stmt = self._select().where(
            ColumnProfile.dataset_version_id == version_id, ColumnProfile.name == name
        )
        return self.session.scalars(stmt).first()


class CleaningLogRepository(TenantRepository[CleaningLog]):
    """Applied cleaning fixes of one tenant."""

    model = CleaningLog

    def for_version(self, version_id: uuid.UUID) -> list[CleaningLog]:
        """Return the fixes that created a version, in the order they were applied."""
        stmt = (
            self._select()
            .where(CleaningLog.version_id == version_id)
            .order_by(CleaningLog.applied_at, CleaningLog.fix_id)
        )
        return list(self.session.scalars(stmt))


class DatasetVersionRepository(TenantRepository[DatasetVersion]):
    """Dataset versions of one tenant."""

    model = DatasetVersion

    def for_dataset(self, dataset_id: uuid.UUID) -> list[DatasetVersion]:
        """Return all versions of a dataset, newest first."""
        stmt = (
            self._select()
            .where(DatasetVersion.dataset_id == dataset_id)
            .order_by(DatasetVersion.version.desc())
        )
        return list(self.session.scalars(stmt))

    def next_version_number(self, dataset_id: uuid.UUID) -> int:
        """Return max(version) + 1 for a dataset (call with the dataset row locked)."""
        stmt = select(func.max(DatasetVersion.version)).where(
            DatasetVersion.tenant_id == self.tenant_id, DatasetVersion.dataset_id == dataset_id
        )
        return (self.session.scalar(stmt) or 0) + 1

    def get_for_query(self, version_id: uuid.UUID) -> DatasetVersion | None:
        """Return a version only if its dataset is not deleted."""
        stmt = (
            self._select()
            .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
            .where(DatasetVersion.id == version_id, Dataset.deleted_at.is_(None))
        )
        return self.session.scalars(stmt).first()
