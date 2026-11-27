"""Idempotency receipts for group match scoring.

Revision ID: ab3100200004
Revises: ab3100200003
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "ab3100200004"
down_revision: str | None = "ab3100200003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "group_game_results",
        sa.Column("match_id", sa.String(32), primary_key=True),
        sa.Column("rating_changes", JSONB, nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("group_game_results")
