"""Store aggregate transfer sizes without 32-bit overflow."""

import sqlalchemy as sa
from alembic import op

revision = "ab3100200001"
down_revision = "a84eb9ddaec6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("in_bytes", "out_bytes"):
        op.alter_column("conversions", name, type_=sa.BigInteger(), existing_type=sa.Integer())


def downgrade() -> None:
    for name in ("in_bytes", "out_bytes"):
        op.alter_column("conversions", name, type_=sa.Integer(), existing_type=sa.BigInteger())
