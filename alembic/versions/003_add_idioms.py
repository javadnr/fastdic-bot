"""Add idioms table

Revision ID: 003
Revises: 002
Create Date: 2025-01-01
"""

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idioms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("word_id", sa.Integer(), sa.ForeignKey("words.id"), nullable=False),
        sa.Column("phrase", sa.Text(), nullable=False),
        sa.Column("meaning", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("idioms")
