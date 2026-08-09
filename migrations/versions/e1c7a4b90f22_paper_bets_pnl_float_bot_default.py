"""paper_bets.pnl_cents to float (fractional cents for decimal units) + fix
stale bot discriminator server_default ('t1' -> 'pre' after the four-bots rename)

Revision ID: e1c7a4b90f22
Revises: d9a3f21c5e07
Create Date: 2026-07-22

"""
import sqlalchemy as sa
from alembic import op

revision = "e1c7a4b90f22"
down_revision = "d9a3f21c5e07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # units is Float; pnl_cents was Integer, silently rounding multi-unit P&L.
    op.alter_column(
        "paper_bets", "pnl_cents",
        existing_type=sa.Integer(),
        type_=sa.Float(),
        existing_nullable=True,
        postgresql_using="pnl_cents::double precision",
    )
    # d9a3f21c5e07 renamed row data t1->pre but left the column's server_default
    # as the now-invalid 't1'; align it so a raw insert omitting bot is valid.
    op.alter_column("paper_bets", "bot", server_default="pre")


def downgrade() -> None:
    op.alter_column("paper_bets", "bot", server_default="t1")
    op.alter_column(
        "paper_bets", "pnl_cents",
        existing_type=sa.Float(),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="round(pnl_cents)::integer",
    )
