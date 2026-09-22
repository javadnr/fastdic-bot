"""Add extra JSONB columns to words table

Revision ID: 005
Revises: 004
Create Date: 2025-01-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("words", sa.Column("verb_forms", JSONB(), nullable=True))
    op.add_column("words", sa.Column("synonyms_antonyms", JSONB(), nullable=True))
    op.add_column("words", sa.Column("phrasal_verbs", JSONB(), nullable=True))
    op.add_column("words", sa.Column("collocations", JSONB(), nullable=True))
    op.add_column("words", sa.Column("related_words", JSONB(), nullable=True))
    op.add_column("words", sa.Column("faq", JSONB(), nullable=True))

    op.execute("UPDATE words SET verb_forms = '{}'::jsonb WHERE verb_forms IS NULL")
    op.execute("UPDATE words SET synonyms_antonyms = '[]'::jsonb WHERE synonyms_antonyms IS NULL")
    op.execute("UPDATE words SET phrasal_verbs = '[]'::jsonb WHERE phrasal_verbs IS NULL")
    op.execute("UPDATE words SET collocations = '[]'::jsonb WHERE collocations IS NULL")
    op.execute("UPDATE words SET related_words = '[]'::jsonb WHERE related_words IS NULL")
    op.execute("UPDATE words SET faq = '[]'::jsonb WHERE faq IS NULL")

    op.alter_column("words", "verb_forms", nullable=False, server_default="{}")
    op.alter_column("words", "synonyms_antonyms", nullable=False, server_default="[]")
    op.alter_column("words", "phrasal_verbs", nullable=False, server_default="[]")
    op.alter_column("words", "collocations", nullable=False, server_default="[]")
    op.alter_column("words", "related_words", nullable=False, server_default="[]")
    op.alter_column("words", "faq", nullable=False, server_default="[]")


def downgrade() -> None:
    op.drop_column("words", "faq")
    op.drop_column("words", "related_words")
    op.drop_column("words", "collocations")
    op.drop_column("words", "phrasal_verbs")
    op.drop_column("words", "synonyms_antonyms")
    op.drop_column("words", "verb_forms")
