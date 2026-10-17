"""datasets and dataset_versions

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datasets")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_datasets_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_datasets_created_by_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(op.f("ix_datasets_tenant_id"), "datasets", ["tenant_id"])
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("original_path", sa.String(500), nullable=False),
        sa.Column("parquet_path", sa.String(500), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=True),
        sa.Column("col_count", sa.Integer(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sheet", sa.String(255), nullable=True),
        sa.Column("encoding", sa.String(50), nullable=True),
        sa.Column("delimiter", sa.String(5), nullable=True),
        sa.Column("column_map", postgresql.JSONB(), nullable=True),
        sa.Column("warnings", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_versions")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_dataset_versions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_dataset_versions_dataset_id_datasets"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status in ('uploaded', 'processing', 'ready', 'failed')",
            name=op.f("ck_dataset_versions_status"),
        ),
        sa.UniqueConstraint("dataset_id", "version", name="uq_dataset_versions_dataset_id_version"),
    )
    op.create_index(op.f("ix_dataset_versions_tenant_id"), "dataset_versions", ["tenant_id"])
    op.create_index(op.f("ix_dataset_versions_dataset_id"), "dataset_versions", ["dataset_id"])
    op.create_foreign_key(
        "fk_datasets_current_version_id_dataset_versions",
        "datasets",
        "dataset_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_datasets_current_version_id_dataset_versions", "datasets", type_="foreignkey"
    )
    op.drop_table("dataset_versions")
    op.drop_table("datasets")
