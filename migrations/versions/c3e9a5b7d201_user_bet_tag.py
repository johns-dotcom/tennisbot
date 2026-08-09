"""User bet tag (tag tailed bets; per-tag performance on My Bets)

Revision ID: c3e9a5b7d201
Revises: b2d8f4a1c6e7
Create Date: 2026-07-31

"""
import sqlalchemy as sa
from alembic import op

revision = "c3e9a5b7d201"
down_revision = "b2d8f4a1c6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_bets", sa.Column("tag", sa.String(length=64), nullable=True))
    op.create_index("ix_user_bets_tag", "user_bets", ["tag"])


def downgrade() -> None:
    op.drop_index("ix_user_bets_tag", table_name="user_bets")
    op.drop_column("user_bets", "tag")
