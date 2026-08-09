"""User tag colors (colour-code My Bets tags)

Revision ID: e5a2c9f37b41
Revises: d4f1b8c26e93
Create Date: 2026-08-01

"""
import sqlalchemy as sa
from alembic import op

revision = "e5a2c9f37b41"
down_revision = "d4f1b8c26e93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_tags",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "tag", name="uq_user_tags_user_tag"),
    )
    op.create_index("ix_user_tags_user_id", "user_tags", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_tags_user_id", table_name="user_tags")
    op.drop_table("user_tags")
