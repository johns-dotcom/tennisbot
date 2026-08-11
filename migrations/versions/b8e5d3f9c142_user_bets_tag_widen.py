"""user_bets: widen tag so several tags fit without truncating one mid-word

The column holds a comma-joined list; at 64 chars a bet tailed from ~5 sources
overflowed and the join was sliced mid-tag, inventing a phantom tag that then
appeared in autocomplete and per-tag performance.

Revision ID: b8e5d3f9c142
Revises: a7c4e1f8b520
Create Date: 2026-08-11

"""
import sqlalchemy as sa
from alembic import op

revision = "b8e5d3f9c142"
down_revision = "a7c4e1f8b520"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("user_bets", "tag",
                    existing_type=sa.String(64), type_=sa.String(256),
                    existing_nullable=True)


def downgrade() -> None:
    # narrowing would fail on any row already using the extra width
    op.execute("UPDATE user_bets SET tag = left(tag, 64) WHERE length(tag) > 64")
    op.alter_column("user_bets", "tag",
                    existing_type=sa.String(256), type_=sa.String(64),
                    existing_nullable=True)
