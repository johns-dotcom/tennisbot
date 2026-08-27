"""kalshi_fills: drop the redundant raw payload

Every field Kalshi returns is already in a typed column except market_ticker
(always == ticker), side (always == outcome_side), subaccount_number (always 0)
and exchange_index — verified across a 500-fill sample. Keeping the JSONB cost
~2.3 kB per fill, i.e. ~24 MB of Python objects across this account's 10.5k
fills every time they were loaded. Railway bills memory.

Revision ID: e3a9c7b21d58
Revises: d8f2a6c31e04
Create Date: 2026-08-27

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "e3a9c7b21d58"
down_revision = "d8f2a6c31e04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("kalshi_fills", "raw")


def downgrade() -> None:
    op.add_column("kalshi_fills", sa.Column("raw", JSONB(), nullable=True))
