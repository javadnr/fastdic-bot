"""Add daily request counters to bot_state

Revision ID: 007
Revises: 006
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bot_state", sa.Column("stats_date", sa.Date(), nullable=True))
    op.add_column("bot_state", sa.Column("fastdic_requests", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("bot_state", sa.Column("db_hits", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("bot_state", "db_hits")
    op.drop_column("bot_state", "fastdic_requests")
    op.drop_column("bot_state", "stats_date")
