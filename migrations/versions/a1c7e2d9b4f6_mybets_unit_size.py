"""My Bets: per-user current unit size + per-bet unit snapshot

Revision ID: a1c7e2d9b4f6
Revises: f6a3c8d2b9e4
Create Date: 2026-07-30

"""
import sqlalchemy as sa
from alembic import op

revision = "a1c7e2d9b4f6"
down_revision = "f6a3c8d2b9e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_users", sa.Column("mybets_unit_usd", sa.Integer(),
                                         nullable=False, server_default="500"))
    op.add_column("user_bets", sa.Column("unit_usd", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_bets", "unit_usd")
    op.drop_column("app_users", "mybets_unit_usd")
