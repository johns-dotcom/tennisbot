"""market_ticks(ts) index — global max(ts)/recency scans

The header feed-status indicator (and other recency reads) run
``SELECT max(ts) FROM market_ticks`` on a ~20M-row table. The existing composite
``(market_ticker, ts)`` index can't serve a global max, so the planner did a
parallel seq scan (~750ms) on every page load. A plain btree on ``ts`` lets it do
an Index-Only-Scan-Backward (~0.1ms).

On production this index was already built with CREATE INDEX CONCURRENTLY (no
table lock against the live recorder); this migration is idempotent
(IF NOT EXISTS) so it's a no-op there and creates the index on fresh databases.

Revision ID: b7d1e9c3f4a2
Revises: a3f5d9c2b7e1
Create Date: 2026-07-28

"""
from alembic import op

revision = "b7d1e9c3f4a2"
down_revision = "a3f5d9c2b7e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_market_ticks_ts "
               "ON market_ticks USING btree (ts)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_market_ticks_ts")
