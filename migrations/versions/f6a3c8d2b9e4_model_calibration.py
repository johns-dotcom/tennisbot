"""model_calibration — persisted, auto-refit calibration params

Revision ID: f6a3c8d2b9e4
Revises: e5c9b3a2f7d1
Create Date: 2026-07-29

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "f6a3c8d2b9e4"
down_revision = "e5c9b3a2f7d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_calibration",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("fitted_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("state_scale", JSONB(), nullable=True),
        sa.Column("detail", JSONB(), nullable=True),
    )
    op.create_index("ix_model_calibration_fitted_at", "model_calibration", ["fitted_at"])


def downgrade() -> None:
    op.drop_index("ix_model_calibration_fitted_at", table_name="model_calibration")
    op.drop_table("model_calibration")
