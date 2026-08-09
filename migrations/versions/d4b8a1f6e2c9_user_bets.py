"""user_bets — per-user manual bet ledger

Revision ID: d4b8a1f6e2c9
Revises: c9e2f4a7d1b3
Create Date: 2026-07-29

"""
import sqlalchemy as sa
from alembic import op

revision = "d4b8a1f6e2c9"
down_revision = "c9e2f4a7d1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_bets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(),
                  sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_ticker", sa.String(length=128), nullable=False),
        sa.Column("market_ticker", sa.String(length=128), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False, server_default="yes"),
        sa.Column("player_name", sa.String(length=128), nullable=False),
        sa.Column("opponent_name", sa.String(length=128), nullable=True),
        sa.Column("entry_price_cents", sa.Integer(), nullable=False),
        sa.Column("shares", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_user_bets_user_id", "user_bets", ["user_id"])
    op.create_index("ix_user_bets_market_ticker", "user_bets", ["market_ticker"])


def downgrade() -> None:
    op.drop_index("ix_user_bets_market_ticker", table_name="user_bets")
    op.drop_index("ix_user_bets_user_id", table_name="user_bets")
    op.drop_table("user_bets")
