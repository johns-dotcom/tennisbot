"""paper_bets.bot discriminator — T1 (fixed policy) vs T2 (self-improving)

Revision ID: c7d2e9f1a840
Revises: b2f4a7c9d310
Create Date: 2026-07-22

"""
import sqlalchemy as sa
from alembic import op

revision = "c7d2e9f1a840"
down_revision = "b2f4a7c9d310"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_bets", sa.Column(
        "bot", sa.String(length=8), nullable=False, server_default="t1"))
    op.create_index("ix_paper_bets_bot", "paper_bets", ["bot"])
    # one bet per event PER BOT (was globally unique on event_ticker), so T1 and
    # T2 can each evaluate the same match once
    op.drop_constraint("paper_bets_event_ticker_key", "paper_bets", type_="unique")
    op.create_unique_constraint("uq_paper_bet_bot_event", "paper_bets",
                                ["bot", "event_ticker"])


def downgrade() -> None:
    op.drop_constraint("uq_paper_bet_bot_event", "paper_bets", type_="unique")
    op.create_unique_constraint("paper_bets_event_ticker_key", "paper_bets",
                                ["event_ticker"])
    op.drop_index("ix_paper_bets_bot", table_name="paper_bets")
    op.drop_column("paper_bets", "bot")
