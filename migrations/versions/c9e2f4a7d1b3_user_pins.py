"""user_pins — per-user pinned matches on the live board

Revision ID: c9e2f4a7d1b3
Revises: b7d1e9c3f4a2
Create Date: 2026-07-28

"""
import sqlalchemy as sa
from alembic import op

revision = "c9e2f4a7d1b3"
down_revision = "b7d1e9c3f4a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_pins",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(),
                  sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_ticker", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "event_ticker", name="uq_user_pins_user_event"),
    )
    op.create_index("ix_user_pins_user_id", "user_pins", ["user_id"])
    op.create_index("ix_user_pins_event_ticker", "user_pins", ["event_ticker"])


def downgrade() -> None:
    op.drop_index("ix_user_pins_event_ticker", table_name="user_pins")
    op.drop_index("ix_user_pins_user_id", table_name="user_pins")
    op.drop_table("user_pins")
