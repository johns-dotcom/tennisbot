"""kalshi_markets result + settled_at

Revision ID: 6b5787a61ff4
Revises: 1f61e0076116
Create Date: 2026-07-19 22:16:41.535276

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '6b5787a61ff4'
down_revision: Union[str, None] = '1f61e0076116'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("kalshi_markets", sa.Column("result", sa.String(8), nullable=True))
    op.add_column("kalshi_markets",
                  sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("kalshi_markets", "settled_at")
    op.drop_column("kalshi_markets", "result")
