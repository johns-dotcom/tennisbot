"""user_bets: partial cash-out (sold slices point back at their position)

Revision ID: a7c4e1f8b520
Revises: f6b3d18ac052
Create Date: 2026-08-09

"""
import sqlalchemy as sa
from alembic import op

revision = "a7c4e1f8b520"
down_revision = "f6b3d18ac052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_bets",
                  sa.Column("parent_bet_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_user_bets_parent_bet_id", "user_bets", ["parent_bet_id"])
    op.create_foreign_key("fk_user_bets_parent", "user_bets", "user_bets",
                          ["parent_bet_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_user_bets_parent", "user_bets", type_="foreignkey")
    op.drop_index("ix_user_bets_parent_bet_id", table_name="user_bets")
    op.drop_column("user_bets", "parent_bet_id")
