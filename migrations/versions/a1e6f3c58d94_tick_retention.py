"""kalshi_markets: durable tick summaries so market_ticks can be pruned

market_ticks had no retention: every websocket quote for every watched market,
kept forever. ~120M rows / ~39 GB after ~8 weeks, and a Postgres working over
that is what held multi-GB of RAM on a memory-billed host (memory was 88% of
the bill).

Before any tick is deleted, each market's peak bids and the LAST time each side
was at/above the take-profit limit are stored here. tp_*_at is what preserves
the "did it reach 90c after I bet?" question exactly — a peak alone cannot,
since the peak may predate the bet.

Revision ID: a1e6f3c58d94
Revises: f4d2b8e01a37
Create Date: 2026-08-27

"""
import sqlalchemy as sa
from alembic import op

revision = "a1e6f3c58d94"
down_revision = "f4d2b8e01a37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in (sa.Column("peak_yes_bid", sa.Integer()),
                sa.Column("peak_no_bid", sa.Integer()),
                sa.Column("tp_yes_at", sa.DateTime(timezone=True)),
                sa.Column("tp_no_at", sa.DateTime(timezone=True)),
                sa.Column("ticks_pruned_at", sa.DateTime(timezone=True))):
        op.add_column("kalshi_markets", col)


def downgrade() -> None:
    for name in ("ticks_pruned_at", "tp_no_at", "tp_yes_at",
                 "peak_no_bid", "peak_yes_bid"):
        op.drop_column("kalshi_markets", name)
