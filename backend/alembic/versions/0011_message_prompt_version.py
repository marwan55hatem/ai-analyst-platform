"""messages.prompt_version: the system prompt version behind each stored answer

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("prompt_version", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "prompt_version")
