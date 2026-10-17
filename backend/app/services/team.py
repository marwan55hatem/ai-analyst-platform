"""Team management rules: roles, last-owner protection, deactivation."""

import uuid

from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError, ForbiddenError
from app.core.password_policy import validate_password
from app.core.security import hash_password
from app.db.repository import UserRepository
from app.models.user import Role, User
from app.services.auth import revoke_user_tokens


def _ensure_can_manage(actor: User, target: User) -> None:
    if target.role == Role.owner and actor.role != Role.owner:
        raise ForbiddenError("Only an owner can change an owner", code="owner_protected")


def _ensure_other_owner_exists(repo: UserRepository, target: User) -> None:
    """Lock the active owners and require one besides `target`."""
    owners = repo.active_owners(lock=True)
    if not any(o.id != target.id for o in owners):
        raise ConflictError("A tenant must keep at least one owner", code="last_owner")


def list_members(repo: UserRepository) -> list[User]:
    """Return all members of the tenant."""
    return repo.list_members()


def create_member(
    repo: UserRepository,
    actor: User,
    *,
    email: str,
    full_name: str,
    password: str,
    role: Role,
) -> User:
    """Create a user in the actor's tenant with an initial password."""
    if role == Role.owner and actor.role != Role.owner:
        raise ForbiddenError("Only an owner can create an owner", code="owner_protected")
    validate_password(password, email=email, full_name=full_name)
    user = repo.add(
        email=email,
        full_name=full_name,
        password_hash=hash_password(password),
        role=role.value,
    )
    try:
        repo.session.commit()
    except IntegrityError as exc:
        repo.session.rollback()
        raise ConflictError("Email is already registered", code="email_taken") from exc
    return user


def change_role(repo: UserRepository, actor: User, member_id: uuid.UUID, new_role: Role) -> User:
    """Change a member's role, keeping at least one owner."""
    repo.active_owners(lock=True)  # always lock owners before the target: one lock order
    target = repo.get_or_404(member_id, for_update=True)
    _ensure_can_manage(actor, target)
    if new_role == Role.owner and actor.role != Role.owner:
        raise ForbiddenError("Only an owner can grant ownership", code="owner_protected")
    if target.role == Role.owner and new_role != Role.owner and target.is_active:
        _ensure_other_owner_exists(repo, target)
    target.role = new_role.value
    repo.session.commit()
    return target


def deactivate_member(repo: UserRepository, actor: User, member_id: uuid.UUID) -> User:
    """Deactivate a member and revoke their refresh tokens."""
    repo.active_owners(lock=True)  # always lock owners before the target: one lock order
    target = repo.get_or_404(member_id, for_update=True)
    _ensure_can_manage(actor, target)
    if target.role == Role.owner and target.is_active:
        _ensure_other_owner_exists(repo, target)
    target.is_active = False
    revoke_user_tokens(repo.session, target.id)
    repo.session.commit()
    return target
