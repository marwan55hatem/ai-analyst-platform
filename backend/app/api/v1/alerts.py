"""Alert endpoints: the feed, the unread count, read state and each company's thresholds."""

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api import responses
from app.api.deps import CurrentUser, DbSession, Limiter, require_role
from app.db.repository import AlertRepository, AlertSettingsRepository
from app.models.user import Role
from app.schemas.alerts import (
    AlertOut,
    AlertPage,
    AlertSettingsIn,
    AlertSettingsOut,
    ReadAllOut,
    UnreadCount,
)
from app.services.alerts import load_thresholds

router = APIRouter(tags=["alerts"], responses={**responses.UNAUTHORIZED})

EDITORS = (Role.owner.value, Role.admin.value)
SETTINGS_WRITES_PER_MINUTE = 20


@router.get("/alerts", response_model=AlertPage, summary="The alert feed, newest first")
def list_alerts(
    user: CurrentUser,
    db: DbSession,
    unread: Annotated[bool, Query(description="Only unread alerts")] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AlertPage:
    """Return one page of the tenant's alerts."""
    rows, total = AlertRepository(db, user.tenant_id).page(
        unread=unread, limit=limit, offset=offset
    )
    return AlertPage(items=[AlertOut.model_validate(r) for r in rows], total=total)


@router.get("/alerts/unread-count", response_model=UnreadCount, summary="Unread alerts")
def unread_count(user: CurrentUser, db: DbSession) -> UnreadCount:
    """Return how many alerts the tenant has not read."""
    return UnreadCount(count=AlertRepository(db, user.tenant_id).unread_count())


@router.post(
    "/alerts/{alert_id}/read",
    response_model=AlertOut,
    summary="Mark one alert read",
    responses={**responses.NOT_FOUND},
)
def mark_read(alert_id: uuid.UUID, user: CurrentUser, db: DbSession) -> AlertOut:
    """Mark an alert read (idempotent)."""
    alert = AlertRepository(db, user.tenant_id).get_or_404(alert_id)
    if alert.read_at is None:
        alert.read_at = datetime.now(UTC)
        db.commit()
    return AlertOut.model_validate(alert)


@router.post("/alerts/read-all", response_model=ReadAllOut, summary="Mark every alert read")
def mark_all_read(user: CurrentUser, db: DbSession) -> ReadAllOut:
    """Mark all of the tenant's unread alerts read."""
    updated = AlertRepository(db, user.tenant_id).mark_all_read()
    db.commit()
    return ReadAllOut(updated=updated)


def _settings_out(user_role: str, db: DbSession, tenant_id: uuid.UUID) -> AlertSettingsOut:
    saved = AlertSettingsRepository(db, tenant_id).current()
    values = asdict(load_thresholds(db, tenant_id))
    return AlertSettingsOut(
        **values,
        is_default=saved is None,
        can_edit=user_role in EDITORS,
        updated_at=saved.updated_at if saved else None,
    )


@router.get(
    "/alert-settings",
    response_model=AlertSettingsOut,
    summary="The company's alert thresholds (every role may read them)",
)
def get_alert_settings(user: CurrentUser, db: DbSession) -> AlertSettingsOut:
    """Return the saved thresholds, or the defaults."""
    return _settings_out(user.role, db, user.tenant_id)


@router.put(
    "/alert-settings",
    response_model=AlertSettingsOut,
    summary="Change the alert thresholds (owner or admin)",
    dependencies=[Depends(require_role(Role.owner, Role.admin))],
    responses={**responses.FORBIDDEN, **responses.UNPROCESSABLE, **responses.RATE_LIMITED},
)
def put_alert_settings(
    body: AlertSettingsIn, user: CurrentUser, db: DbSession, limiter: Limiter
) -> AlertSettingsOut:
    """Save the company's thresholds; they apply to the next analysis run."""
    limiter.hit(f"alert-settings:{user.id}", SETTINGS_WRITES_PER_MINUTE, 60)
    repo = AlertSettingsRepository(db, user.tenant_id)
    row = repo.current()
    if row is None:
        row = repo.add(**body.model_dump(), updated_by=user.id)
    else:
        for key, value in body.model_dump().items():
            setattr(row, key, value)
        row.updated_by = user.id
    db.commit()
    db.refresh(row)
    return _settings_out(user.role, db, user.tenant_id)
