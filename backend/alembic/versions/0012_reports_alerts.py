"""reports, alerts, alert_settings and analysis_runs.summary

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["tenants.id"], name=op.f(f"fk_{table}_tenant_id_tenants"), ondelete="CASCADE"
    )


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("locale", sa.String(5), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("progress", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("file_key", sa.String(300), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'completed', 'failed')",
            name=op.f("ck_reports_status"),
        ),
        sa.CheckConstraint("locale in ('en', 'ar')", name=op.f("ck_reports_locale")),
        _tenant_fk("reports"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_reports_dataset_id_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs.id"],
            name=op.f("fk_reports_run_id_analysis_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_reports_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
    )
    op.create_index(op.f("ix_reports_tenant_id"), "reports", ["tenant_id"])
    op.create_index(op.f("ix_reports_dataset_id"), "reports", ["dataset_id"])
    op.create_index("ix_reports_tenant_created", "reports", ["tenant_id", "created_at"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("rule", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "severity in ('info', 'warning', 'critical')", name=op.f("ck_alerts_severity")
        ),
        _tenant_fk("alerts"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_alerts_dataset_id_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs.id"],
            name=op.f("fk_alerts_run_id_analysis_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_alerts_tenant_dedupe"),
    )
    op.create_index(op.f("ix_alerts_tenant_id"), "alerts", ["tenant_id"])
    op.create_index(op.f("ix_alerts_dataset_id"), "alerts", ["dataset_id"])
    op.create_index("ix_alerts_tenant_created", "alerts", ["tenant_id", "created_at"])

    op.create_table(
        "alert_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("revenue_drop_pct", sa.Float(), nullable=False),
        sa.Column("orders_drop_pct", sa.Float(), nullable=False),
        sa.Column("churn_increase_pct", sa.Float(), nullable=False),
        sa.Column("quality_drop_points", sa.Integer(), nullable=False),
        sa.Column("freshness_enabled", sa.Boolean(), nullable=False),
        sa.Column("freshness_days", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _tenant_fk("alert_settings"),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_alert_settings_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_settings")),
        sa.UniqueConstraint("tenant_id", name="uq_alert_settings_tenant"),
    )
    op.create_index(op.f("ix_alert_settings_tenant_id"), "alert_settings", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("alert_settings")
    op.drop_table("alerts")
    op.drop_table("reports")
    op.drop_column("analysis_runs", "summary")
