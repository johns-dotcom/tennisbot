"""derivative_markets: non-match-winner Kalshi tennis markets (set winner, exact
score, total games ...) so users can log personal bets on them

Its own table on purpose — the bot's pipeline reads kalshi_markets, and the
probability engine models match winner only.

Revision ID: c4f7a2e8d360
Revises: b8e5d3f9c142
Create Date: 2026-08-11

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c4f7a2e8d360"
down_revision = "b8e5d3f9c142"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "derivative_markets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("ticker", sa.String(96), nullable=False, unique=True),
        sa.Column("event_ticker", sa.String(96), nullable=False),
        sa.Column("series_ticker", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("match_event_ticker", sa.String(96), nullable=False),
        sa.Column("set_no", sa.Integer()),
        sa.Column("label", sa.String(160)),
        sa.Column("match_label", sa.String(160)),
        sa.Column("title", sa.Text()),
        sa.Column("status", sa.String(24)),
        sa.Column("close_time", sa.DateTime(timezone=True)),
        sa.Column("yes_bid_cents", sa.Integer()),
        sa.Column("yes_ask_cents", sa.Integer()),
        sa.Column("last_price_cents", sa.Integer()),
        sa.Column("result", sa.String(8)),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("raw", JSONB()),
    )
    op.create_index("ix_derivative_markets_event_ticker",
                    "derivative_markets", ["event_ticker"])
    op.create_index("ix_derivative_markets_kind", "derivative_markets", ["kind"])
    op.create_index("ix_derivative_markets_match_event_ticker",
                    "derivative_markets", ["match_event_ticker"])


def downgrade() -> None:
    op.drop_index("ix_derivative_markets_match_event_ticker",
                  table_name="derivative_markets")
    op.drop_index("ix_derivative_markets_kind", table_name="derivative_markets")
    op.drop_index("ix_derivative_markets_event_ticker",
                  table_name="derivative_markets")
    op.drop_table("derivative_markets")
