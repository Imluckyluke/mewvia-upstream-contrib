"""Instagram cookie ORM model for multi-account pool."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class InstagramCookie(TimestampMixin, Base):
    __tablename__ = "instagram_cookies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. ds_user_id
    cookie_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default="true", index=True
    )
    # status: 'valid', 'invalid', 'untested'
    status: Mapped[str] = mapped_column(
        String(32), default="untested", nullable=False, server_default="untested", index=True
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<InstagramCookie {self.id} label={self.label!r} "
            f"status={self.status} active={self.is_active}>"
        )
