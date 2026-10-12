"""phase 1: users, settings, emoji_slots

Revision ID: 80ea525a28d4
Revises:
Create Date: 2026-08-01 10:38:25.608250
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "80ea525a28d4"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "emoji_slots",
        sa.Column("slot", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("custom_emoji_id", sa.String(length=32), nullable=True),
        sa.Column("fallback", sa.String(length=16), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="100", nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
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
        sa.PrimaryKeyConstraint("slot", name=op.f("pk_emoji_slots")),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
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
        sa.PrimaryKeyConstraint("key", name=op.f("pk_settings")),
    )
    op.create_table(
        "users",
        sa.Column("telegram_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=True),
        sa.Column("last_name", sa.String(length=128), nullable=True),
        sa.Column("language_code", sa.String(length=16), nullable=True),
        sa.Column("is_premium", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_banned", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("has_blocked_bot", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("updates_seen", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("telegram_id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_created_at"), "users", ["created_at"], unique=False)
    op.create_index(op.f("ix_users_has_blocked_bot"), "users", ["has_blocked_bot"], unique=False)
    op.create_index(op.f("ix_users_is_banned"), "users", ["is_banned"], unique=False)
    op.create_index(op.f("ix_users_last_seen_at"), "users", ["last_seen_at"], unique=False)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_index(op.f("ix_users_last_seen_at"), table_name="users")
    op.drop_index(op.f("ix_users_is_banned"), table_name="users")
    op.drop_index(op.f("ix_users_has_blocked_bot"), table_name="users")
    op.drop_index(op.f("ix_users_created_at"), table_name="users")
    op.drop_table("users")
    op.drop_table("settings")
    op.drop_table("emoji_slots")
