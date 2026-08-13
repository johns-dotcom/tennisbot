"""kalshi portfolio: fills, settlements and per-position tags

Read-only mirror of the owner's own Kalshi account (CLAUDE.md rule 1's narrow
exception). Contract counts are FLOAT because Kalshi supports fractional
contracts, and fees are float cents because a single fee is often sub-cent.

Revision ID: d8f2a6c31e04
Revises: c4f7a2e8d360
Create Date: 2026-08-13

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "d8f2a6c31e04"
down_revision = "c4f7a2e8d360"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kalshi_fills",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("fill_id", sa.String(64), nullable=False, unique=True),
        sa.Column("trade_id", sa.String(64)),
        sa.Column("order_id", sa.String(64)),
        sa.Column("ticker", sa.String(96), nullable=False),
        sa.Column("event_ticker", sa.String(96)),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("outcome_side", sa.String(4), nullable=False),
        sa.Column("book_side", sa.String(8)),
        sa.Column("count", sa.Float(), nullable=False),
        sa.Column("yes_price_cents", sa.Integer()),
        sa.Column("no_price_cents", sa.Integer()),
        sa.Column("fee_cents", sa.Float()),
        sa.Column("is_taker", sa.Boolean()),
        sa.Column("ts", sa.BigInteger()),
        sa.Column("created_time", sa.DateTime(timezone=True)),
        sa.Column("raw", JSONB()),
    )
    op.create_index("ix_kalshi_fills_ticker", "kalshi_fills", ["ticker"])
    op.create_index("ix_kalshi_fills_event_ticker", "kalshi_fills", ["event_ticker"])
    op.create_index("ix_kalshi_fills_ts", "kalshi_fills", ["ts"])

    op.create_table(
        "kalshi_settlements",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("ticker", sa.String(96), nullable=False, unique=True),
        sa.Column("event_ticker", sa.String(96)),
        sa.Column("market_result", sa.String(8)),
        sa.Column("yes_count", sa.Float()),
        sa.Column("no_count", sa.Float()),
        sa.Column("revenue_cents", sa.Float()),
        sa.Column("yes_cost_cents", sa.Float()),
        sa.Column("no_cost_cents", sa.Float()),
        sa.Column("fee_cents", sa.Float()),
        sa.Column("settled_time", sa.DateTime(timezone=True)),
        sa.Column("raw", JSONB()),
    )
    op.create_index("ix_kalshi_settlements_event_ticker",
                    "kalshi_settlements", ["event_ticker"])

    op.create_table(
        "kalshi_position_tags",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.BigInteger(),
                  sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("market_ticker", sa.String(96), nullable=False),
        sa.Column("tag", sa.String(256)),
        sa.UniqueConstraint("user_id", "market_ticker",
                            name="uq_kalshi_pos_tag_user_ticker"),
    )
    op.create_index("ix_kalshi_position_tags_user_id",
                    "kalshi_position_tags", ["user_id"])
    op.create_index("ix_kalshi_position_tags_market_ticker",
                    "kalshi_position_tags", ["market_ticker"])


def downgrade() -> None:
    op.drop_table("kalshi_position_tags")
    op.drop_index("ix_kalshi_settlements_event_ticker",
                  table_name="kalshi_settlements")
    op.drop_table("kalshi_settlements")
    op.drop_index("ix_kalshi_fills_ts", table_name="kalshi_fills")
    op.drop_index("ix_kalshi_fills_event_ticker", table_name="kalshi_fills")
    op.drop_index("ix_kalshi_fills_ticker", table_name="kalshi_fills")
    op.drop_table("kalshi_fills")
