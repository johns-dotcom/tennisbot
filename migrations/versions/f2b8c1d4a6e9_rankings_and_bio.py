"""player rankings (current + weekly history) and api-tennis bio/surface splits

Revision ID: f2b8c1d4a6e9
Revises: e1c7a4b90f22
Create Date: 2026-07-24

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "f2b8c1d4a6e9"
down_revision = "e1c7a4b90f22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("players", sa.Column("rank", sa.Integer(), nullable=True))
    op.add_column("players", sa.Column("rank_points", sa.Integer(), nullable=True))
    op.add_column("players", sa.Column("rank_date", sa.Date(), nullable=True))
    op.add_column("players", sa.Column("surface_stats", JSONB(), nullable=True))
    op.add_column("players", sa.Column("bio_synced_at",
                                       sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "player_rankings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("player_id", sa.BigInteger(),
                  sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tour", sa.String(length=8), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.UniqueConstraint("player_id", "as_of", name="uq_player_ranking"),
    )
    op.create_index("ix_player_rankings_player_id", "player_rankings", ["player_id"])


def downgrade() -> None:
    op.drop_index("ix_player_rankings_player_id", table_name="player_rankings")
    op.drop_table("player_rankings")
    op.drop_column("players", "bio_synced_at")
    op.drop_column("players", "surface_stats")
    op.drop_column("players", "rank_date")
    op.drop_column("players", "rank_points")
    op.drop_column("players", "rank")
