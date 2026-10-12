"""phase 4: conversions_table

Revision ID: 99a7183e29f1
Revises: 80ea525a28d4
Create Date: 2026-08-01 18:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "99a7183e29f1"
down_revision: str | None = "80ea525a28d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=32), nullable=False),
        sa.Column("in_bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("out_bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="success", nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversions")),
    )
    op.create_index(op.f("ix_conversions_status"), "conversions", ["status"], unique=False)
    op.create_index(
        op.f("ix_conversions_telegram_id"), "conversions", ["telegram_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_conversions_telegram_id"), table_name="conversions")
    op.drop_index(op.f("ix_conversions_status"), table_name="conversions")
    op.drop_table("conversions")
