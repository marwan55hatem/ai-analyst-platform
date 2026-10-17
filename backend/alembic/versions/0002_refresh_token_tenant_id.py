"""scope refresh tokens by tenant

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("refresh_tokens", sa.Column("tenant_id", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE refresh_tokens SET tenant_id = users.tenant_id "
        "FROM users WHERE users.id = refresh_tokens.user_id"
    )
    op.alter_column("refresh_tokens", "tenant_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_refresh_tokens_tenant_id_tenants"),
        "refresh_tokens",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_refresh_tokens_tenant_id"), "refresh_tokens", ["tenant_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_refresh_tokens_tenant_id"), table_name="refresh_tokens")
    op.drop_constraint(
        op.f("fk_refresh_tokens_tenant_id_tenants"), "refresh_tokens", type_="foreignkey"
    )
    op.drop_column("refresh_tokens", "tenant_id")
