"""user_bets: cash-out (exit) tracking

Revision ID: e5c9b3a2f7d1
Revises: d4b8a1f6e2c9
Create Date: 2026-07-29

"""
import sqlalchemy as sa
from alembic import op

revision = "e5c9b3a2f7d1"
down_revision = "d4b8a1f6e2c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_bets",
                  sa.Column("exit_price_cents", sa.Integer(), nullable=True))
    op.add_column("user_bets",
                  sa.Column("exit_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("user_bets", "exit_at")
    op.drop_column("user_bets", "exit_price_cents")
