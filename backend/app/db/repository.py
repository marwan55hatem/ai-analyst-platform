"""Tenant-scoped repositories: every query is filtered by tenant_id automatically."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, delete, func, insert, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.base import TenantMixin
from app.models.agent import BusinessProfile, Conversation, Message
from app.models.analysis import (
    AnalysisRun,
    AnalysisStatus,
    ChurnScore,
    Insight,
    SegmentAssignment,
)
from app.models.dataset import Dataset, DatasetVersion
from app.models.model_run import ModelRun
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


class AnalysisRunRepository(TenantRepository[AnalysisRun]):
    """Analysis runs of one tenant."""

    model = AnalysisRun

    def active_for_version(self, version_id: uuid.UUID) -> AnalysisRun | None:
        """Return a pending or running run of a version, if any."""
        stmt = self._select().where(
            AnalysisRun.dataset_version_id == version_id,
            AnalysisRun.status.in_([AnalysisStatus.pending.value, AnalysisStatus.running.value]),
        )
        return self.session.scalars(stmt).first()

    def latest_for_version(self, version_id: uuid.UUID) -> AnalysisRun | None:
        """Return the newest run of a version."""
        stmt = (
            self._select()
            .where(AnalysisRun.dataset_version_id == version_id)
            .order_by(AnalysisRun.created_at.desc())
        )
        return self.session.scalars(stmt).first()

    def list_for_dataset(
        self, dataset_id: uuid.UUID, *, version: int | None = None, limit: int = 20
    ) -> list[tuple[AnalysisRun, int, int]]:
        """Return (run, version number, insight count) of a dataset's runs, newest first."""
        count = func.count(Insight.id)
        stmt = (
            select(AnalysisRun, DatasetVersion.version, count)
            .join(DatasetVersion, DatasetVersion.id == AnalysisRun.dataset_version_id)
            .outerjoin(Insight, Insight.run_id == AnalysisRun.id)
            .where(AnalysisRun.tenant_id == self.tenant_id, DatasetVersion.dataset_id == dataset_id)
            .group_by(AnalysisRun.id, DatasetVersion.version)
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id)
            .limit(limit)
        )
        if version is not None:
            stmt = stmt.where(DatasetVersion.version == version)
        return [(run, number, int(n)) for run, number, n in self.session.execute(stmt)]


class InsightRepository(TenantRepository[Insight]):
    """Insights of one tenant."""

    model = Insight

    def for_run(
        self,
        run_id: uuid.UUID,
        *,
        type: str | None = None,
        confidence: str | None = None,
        module: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Insight]:
        """Return a run's insights, highest impact first, with optional filters."""
        stmt = self._select().where(Insight.run_id == run_id)
        if type is not None:
            stmt = stmt.where(Insight.type == type)
        if confidence is not None:
            stmt = stmt.where(Insight.confidence == confidence)
        if module is not None:
            stmt = stmt.where(Insight.module == module)
        stmt = stmt.order_by(Insight.impact_score.desc(), Insight.id).limit(limit).offset(offset)
        return list(self.session.scalars(stmt))

    def delete_for_version(self, version_id: uuid.UUID) -> None:
        """Remove every insight of a version (a re-run replaces them)."""
        self.session.execute(
            delete(Insight).where(
                Insight.tenant_id == self.tenant_id, Insight.dataset_version_id == version_id
            )
        )


class SegmentAssignmentRepository(TenantRepository[SegmentAssignment]):
    """Per-customer segment assignments of one tenant."""

    model = SegmentAssignment

    def add_many(
        self, run_id: uuid.UUID, version_id: uuid.UUID, rows: list[dict[str, Any]]
    ) -> None:
        """Insert a batch of assignments for a run (tenant_id is always set here)."""
        if not rows:
            return
        if any("tenant_id" in r for r in rows):
            raise ValueError("tenant_id is set by the repository")
        fixed = {"tenant_id": self.tenant_id, "run_id": run_id, "dataset_version_id": version_id}
        self.session.execute(insert(SegmentAssignment), [{**r, **fixed} for r in rows])

    def page(
        self,
        run_id: uuid.UUID,
        *,
        segment: str | None = None,
        cluster: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SegmentAssignment], int]:
        """Return one page of a run's assignments (ordered by customer key) and the total."""
        stmt = self._select().where(SegmentAssignment.run_id == run_id)
        if segment is not None:
            stmt = stmt.where(SegmentAssignment.rfm_segment == segment)
        if cluster is not None:
            stmt = stmt.where(SegmentAssignment.cluster == cluster)
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = self.session.scalars(
            stmt.order_by(SegmentAssignment.customer_key, SegmentAssignment.id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows), int(total)

    def delete_for_version(self, version_id: uuid.UUID) -> None:
        """Remove every assignment of a version (a re-run replaces them)."""
        self.session.execute(
            delete(SegmentAssignment).where(
                SegmentAssignment.tenant_id == self.tenant_id,
                SegmentAssignment.dataset_version_id == version_id,
            )
        )


class ChurnScoreRepository(TenantRepository[ChurnScore]):
    """Per-customer churn risk of one tenant."""

    model = ChurnScore

    def add_many(
        self, run_id: uuid.UUID, version_id: uuid.UUID, rows: list[dict[str, Any]]
    ) -> None:
        """Insert a batch of scores for a run (tenant_id is always set here)."""
        if not rows:
            return
        if any("tenant_id" in r for r in rows):
            raise ValueError("tenant_id is set by the repository")
        fixed = {"tenant_id": self.tenant_id, "run_id": run_id, "dataset_version_id": version_id}
        self.session.execute(insert(ChurnScore), [{**r, **fixed} for r in rows])

    def page(
        self,
        run_id: uuid.UUID,
        *,
        risk_band: str | None = None,
        rank_band: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ChurnScore], int]:
        """Return one page of a run's scores (highest estimated risk first) and the total."""
        stmt = self._select().where(ChurnScore.run_id == run_id)
        if risk_band is not None:
            stmt = stmt.where(ChurnScore.risk_band == risk_band)
        if rank_band is not None:
            stmt = stmt.where(ChurnScore.rank_band == rank_band)
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = self.session.scalars(
            stmt.order_by(ChurnScore.probability.desc(), ChurnScore.customer_key, ChurnScore.id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows), int(total)

    def delete_for_version(self, version_id: uuid.UUID) -> None:
        """Remove every score of a version (a re-run replaces them)."""
        self.session.execute(
            delete(ChurnScore).where(
                ChurnScore.tenant_id == self.tenant_id,
                ChurnScore.dataset_version_id == version_id,
            )
        )


class ModelRunRepository(TenantRepository[ModelRun]):
    """Custom model runs (forecasts) of one tenant."""

    model = ModelRun

    def active_for_version(self, version_id: uuid.UUID, kind: str) -> ModelRun | None:
        """Return a pending or running run of this kind for a version, if any."""
        stmt = self._select().where(
            ModelRun.dataset_version_id == version_id,
            ModelRun.kind == kind,
            ModelRun.status.in_(["pending", "running"]),
        )
        return self.session.scalars(stmt).first()


class BusinessProfileRepository(TenantRepository[BusinessProfile]):
    """The company profile of one tenant (at most one row)."""

    model = BusinessProfile

    def current(self) -> BusinessProfile | None:
        """Return the tenant's profile, or None when it was never saved."""
        return self.session.scalars(self._select()).first()


class ConversationRepository(TenantRepository[Conversation]):
    """Chat conversations of one tenant."""

    model = Conversation

    def list_recent(
        self, *, user_id: uuid.UUID | None = None, limit: int = 50, offset: int = 0
    ) -> list[Conversation]:
        """Return conversations, most recently updated first (optionally one user's)."""
        stmt = self._select()
        if user_id is not None:
            stmt = stmt.where(Conversation.user_id == user_id)
        stmt = stmt.order_by(Conversation.updated_at.desc(), Conversation.id)
        return list(self.session.scalars(stmt.limit(limit).offset(offset)))


class MessageRepository(TenantRepository[Message]):
    """Messages of one tenant's conversations."""

    model = Message

    def for_conversation(
        self, conversation_id: uuid.UUID, *, after_seq: int = -1, limit: int | None = None
    ) -> list[Message]:
        """Return a conversation's messages in order (optionally only those after a seq)."""
        stmt = (
            self._select()
            .where(Message.conversation_id == conversation_id, Message.seq > after_seq)
            .order_by(Message.seq)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.scalars(stmt))

    def next_seq(self, conversation_id: uuid.UUID) -> int:
        """Return the next sequence number in a conversation."""
        stmt = select(func.coalesce(func.max(Message.seq), -1)).where(
            Message.tenant_id == self.tenant_id, Message.conversation_id == conversation_id
        )
        return int(self.session.scalar(stmt) or 0) + 1

    def count(self, conversation_id: uuid.UUID) -> int:
        """Return how many messages a conversation has."""
        stmt = select(func.count(Message.id)).where(
            Message.tenant_id == self.tenant_id, Message.conversation_id == conversation_id
        )
        return int(self.session.scalar(stmt) or 0)
