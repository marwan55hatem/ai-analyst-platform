"""Team management endpoints (owner and admin only)."""

import uuid

from fastapi import APIRouter, Depends, status

from app.api import responses
from app.api.deps import CurrentUser, UserRepo, require_role
from app.models.user import Role
from app.schemas.team import MemberCreate, RoleUpdate
from app.schemas.user import UserOut
from app.services import team as team_service

router = APIRouter(
    prefix="/team",
    tags=["team"],
    dependencies=[Depends(require_role(Role.owner, Role.admin))],
    responses={**responses.UNAUTHORIZED, **responses.FORBIDDEN},
)


@router.get("/members", response_model=list[UserOut], summary="List team members")
def list_members(repo: UserRepo) -> list[UserOut]:
    """Return every member of the caller's tenant."""
    return [UserOut.model_validate(u) for u in team_service.list_members(repo)]


@router.post(
    "/members",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a member with an initial password",
    responses={**responses.CONFLICT, **responses.UNPROCESSABLE},
)
def create_member(body: MemberCreate, actor: CurrentUser, repo: UserRepo) -> UserOut:
    """Create a user in the caller's tenant."""
    user = team_service.create_member(
        repo,
        actor,
        email=body.email,
        full_name=body.full_name,
        password=body.password,
        role=body.role,
    )
    return UserOut.model_validate(user)


@router.put(
    "/members/{member_id}/role",
    response_model=UserOut,
    summary="Change a member's role",
    responses={**responses.NOT_FOUND, **responses.CONFLICT},
)
def change_role(
    member_id: uuid.UUID, body: RoleUpdate, actor: CurrentUser, repo: UserRepo
) -> UserOut:
    """Change a role; owners are protected from admins and the last owner is kept."""
    return UserOut.model_validate(team_service.change_role(repo, actor, member_id, body.role))


@router.post(
    "/members/{member_id}/deactivate",
    response_model=UserOut,
    summary="Deactivate a member",
    responses={**responses.NOT_FOUND, **responses.CONFLICT},
)
def deactivate_member(member_id: uuid.UUID, actor: CurrentUser, repo: UserRepo) -> UserOut:
    """Deactivate a member and revoke their sessions."""
    return UserOut.model_validate(team_service.deactivate_member(repo, actor, member_id))
