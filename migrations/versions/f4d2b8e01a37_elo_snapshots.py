"""elo_snapshots: persist the fitted ratings so only the ingest replays history

fit_from_db materialises ~875k match rows plus per-match set results — ~330 MB
peak, measured. Doing that inside the web process to render two spotlight bands
made it expensive on a memory-billed host, because CPython keeps most of a
freed heap resident. The daily ingest already performs this fit, so it now
writes the result here and every other process loads it.

Revision ID: f4d2b8e01a37
Revises: e3a9c7b21d58
Create Date: 2026-08-27

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "f4d2b8e01a37"
down_revision = "e3a9c7b21d58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "elo_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("fitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trained_through", sa.Date()),
        sa.Column("n_matches", sa.Integer()),
        sa.Column("n_players", sa.Integer()),
        sa.Column("ratings", JSONB(), nullable=False),
    )
    op.create_index("ix_elo_snapshots_fitted_at", "elo_snapshots", ["fitted_at"])


def downgrade() -> None:
    op.drop_index("ix_elo_snapshots_fitted_at", table_name="elo_snapshots")
    op.drop_table("elo_snapshots")
