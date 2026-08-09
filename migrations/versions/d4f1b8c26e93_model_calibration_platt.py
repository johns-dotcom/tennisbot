"""model_calibration: persisted global Platt scalar (auto-refit recalibration)

Revision ID: d4f1b8c26e93
Revises: c3e9a5b7d201
Create Date: 2026-08-01

"""
import sqlalchemy as sa
from alembic import op

revision = "d4f1b8c26e93"
down_revision = "c3e9a5b7d201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_calibration", sa.Column("platt_a", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_calibration", "platt_a")
