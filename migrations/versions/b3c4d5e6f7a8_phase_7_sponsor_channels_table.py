"""phase 7: sponsor_channels table

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sponsor_channels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("invite_link", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sponsor_channels")),
        sa.UniqueConstraint("channel_id", name=op.f("uq_sponsor_channels_channel_id")),
    )
    op.create_index(
        op.f("ix_sponsor_channels_channel_id"),
        "sponsor_channels",
        ["channel_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_sponsor_channels_is_active"),
        "sponsor_channels",
        ["is_active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sponsor_channels_username"),
        "sponsor_channels",
        ["username"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sponsor_channels_username"), table_name="sponsor_channels")
    op.drop_index(op.f("ix_sponsor_channels_is_active"), table_name="sponsor_channels")
    op.drop_index(op.f("ix_sponsor_channels_channel_id"), table_name="sponsor_channels")
    op.drop_table("sponsor_channels")
