"""initial

Revision ID: 001
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.Integer(), unique=True, nullable=False),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("first_name", sa.String(), nullable=True),
        sa.Column("last_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("last_searched_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "words",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("word", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("phonetic", sa.String(), nullable=True),
        sa.Column("audio_url_us", sa.String(), nullable=True),
        sa.Column("audio_url_uk", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("word", "direction", name="uq_word_direction"),
    )

    op.create_table(
        "meanings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("word_id", sa.Integer(), sa.ForeignKey("words.id"), nullable=False),
        sa.Column("part_of_speech", sa.String(), nullable=True),
        sa.Column("persian_text", sa.Text(), nullable=False),
        sa.Column("cefr_level", sa.String(), nullable=True),
    )

    op.create_table(
        "example_sentences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("meaning_id", sa.Integer(), sa.ForeignKey("meanings.id"), nullable=False),
        sa.Column("english", sa.Text(), nullable=False),
        sa.Column("persian", sa.Text(), nullable=False),
    )

    op.create_table(
        "searches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("word_id", sa.Integer(), sa.ForeignKey("words.id"), nullable=False),
        sa.Column("searched_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("searches")
    op.drop_table("example_sentences")
    op.drop_table("meanings")
    op.drop_table("words")
    op.drop_table("users")
