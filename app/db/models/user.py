"""User records.

`telegram_id` is the primary key: Telegram already guarantees uniqueness, and
every lookup in the bot starts from it, so a surrogate id would only add a join.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)

    # Profile fields, refreshed on every update we see.
    username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)

    #: Telegram Premium subscriber. Not our own premium-emoji switch.
    is_premium: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

    #: Banned by an admin — the bot refuses to serve them.
    is_banned: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false", index=True
    )

    #: They blocked the bot (detected when a send fails with 403). Broadcasts skip these.
    has_blocked_bot: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false", index=True
    )

    #: Number of updates received from this user; a cheap activity signal.
    #: NOTE: plain group chatter (non-command messages in groups) never reaches
    #: the upsert, so this counts real bot interactions, not group noise.
    updates_seen: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )

    #: True once the user completes at least one successful conversion.
    #: Set by ConversionRepo.log_conversion; backfilled from history at
    #: migration time. This is the "real user" signal the admin stats use —
    #: group chatter and gate-blocked /start taps never flip it.
    has_used_bot: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false", index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    @property
    def display_name(self) -> str:
        """Best available human label, never empty."""
        if self.username:
            return f"@{self.username}"
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) if parts else str(self.telegram_id)

    def __repr__(self) -> str:
        return f"<User {self.telegram_id} {self.display_name}>"
