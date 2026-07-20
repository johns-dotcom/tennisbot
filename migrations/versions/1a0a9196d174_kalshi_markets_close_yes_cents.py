"""kalshi_markets close_yes_cents

Revision ID: 1a0a9196d174
Revises: 742bed9b24c4
Create Date: 2026-07-20 11:48:19.019938

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '1a0a9196d174'
down_revision: Union[str, None] = '742bed9b24c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("kalshi_markets", sa.Column("close_yes_cents", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("kalshi_markets", "close_yes_cents")
