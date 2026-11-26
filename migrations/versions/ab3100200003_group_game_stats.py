"""add_group_game_stats

Revision ID: ab3100200003
Revises: ab3100200002
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ab3100200003"
down_revision: str | None = "ab3100200002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "group_game_stats",
        sa.Column("chat_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column(
            "game_type",
            sa.String(length=32),
            server_default="xo",
            nullable=False,
        ),
        sa.Column("telegram_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("score", sa.Integer(), server_default="1000", nullable=False),
        sa.Column("wins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("losses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("draws", sa.Integer(), server_default="0", nullable=False),
        sa.Column("current_streak", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_streak", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_played_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "chat_id",
            "game_type",
            "telegram_id",
            name=op.f("pk_group_game_stats"),
        ),
    )
    op.create_index(
        op.f("ix_group_game_stats_score"), "group_game_stats", ["score"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_group_game_stats_score"), table_name="group_game_stats")
    op.drop_table("group_game_stats")
