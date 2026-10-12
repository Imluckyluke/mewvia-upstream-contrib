"""Sticker sets ORM model."""

from __future__ import annotations

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class StickerSet(TimestampMixin, Base):
    __tablename__ = "sticker_sets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    sticker_type: Mapped[str] = mapped_column(
        String(16), default="regular", server_default="regular", nullable=False
    )  # regular, custom_emoji
    stickers_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    def __repr__(self) -> str:
        return f"<StickerSet {self.name} user={self.telegram_id} count={self.stickers_count}>"
