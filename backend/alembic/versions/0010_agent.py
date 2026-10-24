"""agent: business_profiles, conversations, messages, agent_usage_daily

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["tenants.id"], name=op.f(f"fk_{table}_tenant_id_tenants"), ondelete="CASCADE"
    )


def upgrade() -> None:
    op.create_table(
        "business_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("industry", sa.String(100), nullable=False, server_default=""),
        sa.Column("goals", sa.Text(), nullable=False, server_default=""),
        sa.Column("currency", sa.String(3), nullable=False, server_default=""),
        sa.Column("language", sa.String(2), nullable=False, server_default="en"),
        sa.Column("constraints", sa.Text(), nullable=False, server_default=""),
        sa.Column("allow_pii", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _tenant_fk("business_profiles"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_profiles")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_business_profiles_tenant_id")),
    )
    op.create_index(op.f("ix_business_profiles_tenant_id"), "business_profiles", ["tenant_id"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("summarized_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _tenant_fk("conversations"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_conversations_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_conversations_dataset_id_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(op.f("ix_conversations_tenant_id"), "conversations", ["tenant_id"])
    op.create_index(op.f("ix_conversations_dataset_id"), "conversations", ["dataset_id"])
    op.create_index(
        "ix_conversations_tenant_updated", "conversations", ["tenant_id", "updated_at"]
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tool_call_id", sa.String(100), nullable=True),
        sa.Column("tool_name", sa.String(50), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("caveats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_cached", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_reasoning", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd_approx", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("role in ('user', 'assistant', 'tool')", name=op.f("ck_messages_role")),
        _tenant_fk("messages"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(op.f("ix_messages_tenant_id"), "messages", ["tenant_id"])
    op.create_index("ix_messages_conversation_seq", "messages", ["conversation_id", "seq"])

    op.create_table(
        "agent_usage_daily",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        _tenant_fk("agent_usage_daily"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_usage_daily")),
        sa.UniqueConstraint("tenant_id", "day", name="uq_agent_usage_daily_tenant_day"),
    )
    op.create_index(op.f("ix_agent_usage_daily_tenant_id"), "agent_usage_daily", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("agent_usage_daily")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("business_profiles")
