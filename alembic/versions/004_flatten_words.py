"""Flatten word data to single table with JSONB

Revision ID: 004
Revises: 003
Create Date: 2025-01-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("words", sa.Column("meanings", JSONB(), nullable=True))
    op.add_column("words", sa.Column("idioms", JSONB(), nullable=True))

    op.execute("""
        UPDATE words w
        SET meanings = (
            SELECT COALESCE(jsonb_agg(
                jsonb_build_object(
                    'text', m.persian_text,
                    'pos', m.part_of_speech,
                    'cefr', m.cefr_level,
                    'examples', (
                        SELECT COALESCE(jsonb_agg(
                            jsonb_build_object('english', es.english, 'persian', es.persian)
                        ), '[]'::jsonb)
                        FROM example_sentences es
                        WHERE es.meaning_id = m.id
                    )
                )
                ORDER BY m.id
            ), '[]'::jsonb)
            FROM meanings m
            WHERE m.word_id = w.id
        )
    """)

    op.execute("""
        UPDATE words w
        SET idioms = (
            SELECT COALESCE(jsonb_agg(
                jsonb_build_object('phrase', i.phrase, 'meaning', i.meaning)
                ORDER BY i.id
            ), '[]'::jsonb)
            FROM idioms i
            WHERE i.word_id = w.id
        )
    """)

    op.alter_column("words", "meanings", nullable=False, server_default="[]")
    op.alter_column("words", "idioms", nullable=False, server_default="[]")

    op.drop_table("example_sentences")
    op.drop_table("meanings")
    op.drop_table("idioms")


def downgrade() -> None:
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
        "idioms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("word_id", sa.Integer(), sa.ForeignKey("words.id"), nullable=False),
        sa.Column("phrase", sa.Text(), nullable=False),
        sa.Column("meaning", sa.Text(), nullable=False),
    )

    op.drop_column("words", "meanings")
    op.drop_column("words", "idioms")
