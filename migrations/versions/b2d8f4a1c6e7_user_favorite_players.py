"""User favorite players (mark favorites on database & live pages)

Revision ID: b2d8f4a1c6e7
Revises: a1c7e2d9b4f6
Create Date: 2026-07-30

"""
import sqlalchemy as sa
from alembic import op

revision = "b2d8f4a1c6e7"
down_revision = "a1c7e2d9b4f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_favorite_players",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "player_id",
                            name="uq_user_fav_players_user_player"),
    )
    op.create_index("ix_user_favorite_players_user_id",
                    "user_favorite_players", ["user_id"])
    op.create_index("ix_user_favorite_players_player_id",
                    "user_favorite_players", ["player_id"])


def downgrade() -> None:
    op.drop_index("ix_user_favorite_players_player_id",
                  table_name="user_favorite_players")
    op.drop_index("ix_user_favorite_players_user_id",
                  table_name="user_favorite_players")
    op.drop_table("user_favorite_players")
