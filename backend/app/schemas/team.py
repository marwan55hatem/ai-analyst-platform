"""Team management request schemas."""

from app.models.user import Role
from app.schemas.user import Email, FullName, Password, StrictModel


class MemberCreate(StrictModel):
    """Create a member with an initial password."""

    email: Email
    full_name: FullName
    password: Password
    role: Role = Role.member


class RoleUpdate(StrictModel):
    """Change a member's role."""

    role: Role
