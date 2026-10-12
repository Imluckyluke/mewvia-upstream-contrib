"""Premium emoji slots.

The bot never hardcodes a custom_emoji_id. Every decorative emoji is referenced
by a stable *slot* name (`ok`, `error`, `warning`, …); the admin panel maps slots
to premium emoji ids at runtime. `fallback` is the plain unicode emoji used when
premium emoji are disabled, unavailable, or rejected by Telegram — so the UI
degrades instead of breaking.

This mirrors the reference panel: one row per slot, each editable and deletable.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class EmojiSlot(TimestampMixin, Base):
    __tablename__ = "emoji_slots"

    #: Stable identifier used in code, e.g. "ok". Never renamed.
    slot: Mapped[str] = mapped_column(String(32), primary_key=True)

    #: Persian label shown in the admin panel, e.g. "تایید / موفق".
    title: Mapped[str] = mapped_column(Text, nullable=False)

    #: Telegram custom emoji id. NULL means "no premium emoji set, use fallback".
    custom_emoji_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: Plain unicode emoji. Always present — this is what makes degradation safe.
    fallback: Mapped[str] = mapped_column(String(16), nullable=False)

    sort_order: Mapped[int] = mapped_column(
        Integer, default=100, nullable=False, server_default="100"
    )

    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    @property
    def is_premium(self) -> bool:
        return self.custom_emoji_id is not None

    def __repr__(self) -> str:
        return f"<EmojiSlot {self.slot} premium={self.is_premium}>"
