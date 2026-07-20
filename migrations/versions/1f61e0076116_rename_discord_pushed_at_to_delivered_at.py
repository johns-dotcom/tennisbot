"""rename discord_pushed_at to delivered_at

Revision ID: 1f61e0076116
Revises: be93d41a9f93
Create Date: 2026-07-19 21:25:29.736040

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '1f61e0076116'
down_revision: Union[str, None] = 'be93d41a9f93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("advisories", "discord_pushed_at", new_column_name="delivered_at")


def downgrade() -> None:
    op.alter_column("advisories", "delivered_at", new_column_name="discord_pushed_at")
