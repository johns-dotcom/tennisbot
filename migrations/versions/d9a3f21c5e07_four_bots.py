"""split the 2 bots into 4 by basis: pre / preSI / live / liveSI

Revision ID: d9a3f21c5e07
Revises: c7d2e9f1a840
Create Date: 2026-07-22

t1/t2 each bet both pre-game and live; now each (bot, basis) pair is its own
bot. Rename existing bets accordingly. No unique-constraint conflict: the old
(bot, event) uniqueness meant each old bot had at most one bet per event, so
each renamed bot inherits at most one per event.
"""
from alembic import op

revision = "d9a3f21c5e07"
down_revision = "c7d2e9f1a840"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE paper_bets SET bot = CASE
          WHEN bot='t1' AND basis='prematch' THEN 'pre'
          WHEN bot='t2' AND basis='prematch' THEN 'preSI'
          WHEN bot='t1' AND basis='advisory' THEN 'live'
          WHEN bot='t2' AND basis='advisory' THEN 'liveSI'
          ELSE bot END
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE paper_bets SET bot = CASE
          WHEN bot IN ('pre','live') THEN 't1'
          WHEN bot IN ('preSI','liveSI') THEN 't2'
          ELSE bot END
    """)
