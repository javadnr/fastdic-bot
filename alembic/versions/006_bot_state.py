"""Add bot_state table for maintenance mode

Revision ID: 006
Revises: 005
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("maintenance", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.execute("INSERT INTO bot_state (id, maintenance) VALUES (1, false)")


def downgrade() -> None:
    op.drop_table("bot_state")
