"""profile stage, column_profiles and cleaning_logs

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

COLUMN_TYPES = "'integer', 'float', 'boolean', 'datetime', 'categorical', 'text', 'identifier'"
ROLES = (
    "'date', 'revenue', 'quantity', 'price', 'cost', 'discount', 'customer_id', 'order_id', "
    "'product', 'category', 'region', 'channel', 'status', 'email', 'phone', 'churn_flag', 'other'"
)


def upgrade() -> None:
    op.add_column(
        "dataset_versions",
        sa.Column("profile_status", sa.String(20), nullable=False, server_default="pending"),
    )
    op.add_column("dataset_versions", sa.Column("profile_error", sa.Text(), nullable=True))
    op.add_column(
        "dataset_versions",
        sa.Column("profile_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("dataset_versions", sa.Column("quality_score", sa.Integer(), nullable=True))
    op.add_column("dataset_versions", sa.Column("quality", postgresql.JSONB(), nullable=True))
    op.add_column(
        "dataset_versions", sa.Column("dataset_profile", postgresql.JSONB(), nullable=True)
    )
    op.add_column("dataset_versions", sa.Column("cleaning_plan", postgresql.JSONB(), nullable=True))
    op.add_column("dataset_versions", sa.Column("source_version_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_dataset_versions_source_version_id_dataset_versions"),
        "dataset_versions",
        "dataset_versions",
        ["source_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_dataset_versions_profile_status"),
        "dataset_versions",
        "profile_status in ('pending', 'running', 'ready', 'failed')",
    )

    op.create_table(
        "column_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("original_name", sa.String(500), nullable=False),
        sa.Column("inferred_type", sa.String(20), nullable=False),
        sa.Column("semantic_role", sa.String(20), nullable=False),
        sa.Column("role_confidence", sa.Float(), nullable=False),
        sa.Column("role_source", sa.String(10), nullable=False),
        sa.Column("stats", postgresql.JSONB(), nullable=False),
        sa.Column("pii_flag", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_column_profiles")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_column_profiles_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name=op.f("fk_column_profiles_dataset_version_id_dataset_versions"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("dataset_version_id", "name", name="uq_column_profiles_version_name"),
        sa.CheckConstraint(
            f"inferred_type in ({COLUMN_TYPES})", name=op.f("ck_column_profiles_inferred_type")
        ),
        sa.CheckConstraint(
            f"semantic_role in ({ROLES})", name=op.f("ck_column_profiles_semantic_role")
        ),
        sa.CheckConstraint(
            "role_source in ('rule', 'llm', 'user')", name=op.f("ck_column_profiles_role_source")
        ),
    )
    op.create_index(op.f("ix_column_profiles_tenant_id"), "column_profiles", ["tenant_id"])
    op.create_index(
        op.f("ix_column_profiles_dataset_version_id"), "column_profiles", ["dataset_version_id"]
    )

    op.create_table(
        "cleaning_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("source_version_id", sa.Uuid(), nullable=True),
        sa.Column("fix_id", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("rows_affected", sa.BigInteger(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cleaning_logs")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_cleaning_logs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["dataset_versions.id"],
            name=op.f("fk_cleaning_logs_version_id_dataset_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["dataset_versions.id"],
            name=op.f("fk_cleaning_logs_source_version_id_dataset_versions"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(op.f("ix_cleaning_logs_tenant_id"), "cleaning_logs", ["tenant_id"])
    op.create_index(op.f("ix_cleaning_logs_version_id"), "cleaning_logs", ["version_id"])


def downgrade() -> None:
    op.drop_table("cleaning_logs")
    op.drop_table("column_profiles")
    op.drop_constraint(
        op.f("ck_dataset_versions_profile_status"), "dataset_versions", type_="check"
    )
    op.drop_constraint(
        op.f("fk_dataset_versions_source_version_id_dataset_versions"),
        "dataset_versions",
        type_="foreignkey",
    )
    for column in (
        "source_version_id",
        "cleaning_plan",
        "dataset_profile",
        "quality",
        "quality_score",
        "profile_started_at",
        "profile_error",
        "profile_status",
    ):
        op.drop_column("dataset_versions", column)
