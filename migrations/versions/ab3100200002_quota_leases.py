"""Short-lived quota reservations across bot and worker instances."""

import sqlalchemy as sa
from alembic import op

revision = "ab3100200002"
down_revision = "ab3100200001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quota_leases",
        sa.Column("token", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in ("user_id", "expires_at"):
        op.create_index(f"ix_quota_leases_{name}", "quota_leases", [name])


def downgrade() -> None:
    op.drop_table("quota_leases")
