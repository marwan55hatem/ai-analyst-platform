"""segment_assignments: per-customer RFM segment and cluster of an analysis run

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "segment_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("customer_key", sa.String(200), nullable=False),
        sa.Column("rfm_segment", sa.String(40), nullable=False),
        sa.Column("r_score", sa.SmallInteger(), nullable=False),
        sa.Column("f_score", sa.SmallInteger(), nullable=False),
        sa.Column("m_score", sa.SmallInteger(), nullable=False),
        sa.Column("recency_days", sa.Integer(), nullable=False),
        sa.Column("frequency", sa.Integer(), nullable=False),
        sa.Column("monetary", sa.Numeric(24, 4), nullable=False),
        sa.Column("cluster", sa.SmallInteger(), nullable=True),
        sa.Column("cluster_name", sa.String(80), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_segment_assignments_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs.id"],
            name=op.f("fk_segment_assignments_run_id_analysis_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name=op.f("fk_segment_assignments_dataset_version_id_dataset_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_segment_assignments")),
    )
    op.create_index(op.f("ix_segment_assignments_tenant_id"), "segment_assignments", ["tenant_id"])
    op.create_index(op.f("ix_segment_assignments_run_id"), "segment_assignments", ["run_id"])
    op.create_index(
        op.f("ix_segment_assignments_dataset_version_id"),
        "segment_assignments",
        ["dataset_version_id"],
    )
    op.create_index(
        "ix_segment_assignments_run_segment",
        "segment_assignments",
        ["tenant_id", "run_id", "rfm_segment"],
    )


def downgrade() -> None:
    op.drop_table("segment_assignments")
