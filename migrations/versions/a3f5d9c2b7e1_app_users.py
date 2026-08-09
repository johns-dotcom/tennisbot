"""app_users — web interface accounts (login gating + admin-managed users)

Revision ID: a3f5d9c2b7e1
Revises: f2b8c1d4a6e9
Create Date: 2026-07-25

"""
import sqlalchemy as sa
from alembic import op

revision = "a3f5d9c2b7e1"
down_revision = "f2b8c1d4a6e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("username", name="uq_app_users_username"),
    )
    op.create_index("ix_app_users_username", "app_users", ["username"])


def downgrade() -> None:
    op.drop_index("ix_app_users_username", table_name="app_users")
    op.drop_table("app_users")
