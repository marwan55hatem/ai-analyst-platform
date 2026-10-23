"""churn_scores: per-customer estimated churn risk of an analysis run

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "churn_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("customer_key", sa.String(200), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("risk_band", sa.String(10), nullable=False),
        sa.Column("rank_band", sa.String(20), nullable=False),
        sa.Column("revenue_at_stake", sa.Numeric(24, 4), nullable=False),
        sa.Column("expected_loss", sa.Numeric(24, 4), nullable=False),
        sa.Column("recency_days", sa.Integer(), nullable=False),
        sa.Column("frequency", sa.Integer(), nullable=False),
        sa.Column("monetary", sa.Numeric(24, 4), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_churn_scores_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs.id"],
            name=op.f("fk_churn_scores_run_id_analysis_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name=op.f("fk_churn_scores_dataset_version_id_dataset_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_churn_scores")),
    )
    op.create_index(op.f("ix_churn_scores_tenant_id"), "churn_scores", ["tenant_id"])
    op.create_index(op.f("ix_churn_scores_run_id"), "churn_scores", ["run_id"])
    op.create_index(
        op.f("ix_churn_scores_dataset_version_id"), "churn_scores", ["dataset_version_id"]
    )
    op.create_index(
        "ix_churn_scores_run_band", "churn_scores", ["tenant_id", "run_id", "risk_band"]
    )


def downgrade() -> None:
    op.drop_table("churn_scores")
