"""phase 6: sticker_sets_table

Revision ID: a1b2c3d4e5f6
Revises: 99a7183e29f1
Create Date: 2026-08-02 00:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "99a7183e29f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sticker_sets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("sticker_type", sa.String(length=16), server_default="regular", nullable=False),
        sa.Column("stickers_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sticker_sets")),
        sa.UniqueConstraint("name", name=op.f("uq_sticker_sets_name")),
    )
    op.create_index(op.f("ix_sticker_sets_name"), "sticker_sets", ["name"], unique=True)
    op.create_index(
        op.f("ix_sticker_sets_telegram_id"), "sticker_sets", ["telegram_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sticker_sets_telegram_id"), table_name="sticker_sets")
    op.drop_index(op.f("ix_sticker_sets_name"), table_name="sticker_sets")
    op.drop_table("sticker_sets")
