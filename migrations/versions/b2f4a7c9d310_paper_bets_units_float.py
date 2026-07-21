"""paper_bets.units to float — decimal, confidence-driven unit sizing

Revision ID: b2f4a7c9d310
Revises: 1a0a9196d174
Create Date: 2026-07-22

"""
import sqlalchemy as sa
from alembic import op

revision = "b2f4a7c9d310"
down_revision = "1a0a9196d174"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "paper_bets", "units",
        existing_type=sa.Integer(),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using="units::double precision",
    )


def downgrade() -> None:
    op.alter_column(
        "paper_bets", "units",
        existing_type=sa.Float(),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="round(units)::integer",
    )
