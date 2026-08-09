"""User tag per-tag unit size

Revision ID: f6b3d18ac052
Revises: e5a2c9f37b41
Create Date: 2026-08-01

"""
import sqlalchemy as sa
from alembic import op

revision = "f6b3d18ac052"
down_revision = "e5a2c9f37b41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_tags", sa.Column("unit_usd", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_tags", "unit_usd")
