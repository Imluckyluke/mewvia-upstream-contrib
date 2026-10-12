"""Conversion records ORM model."""

from __future__ import annotations

from sqlalchemy import BigInteger, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Conversion(TimestampMixin, Base):
    __tablename__ = "conversions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(32), nullable=False)
    in_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    out_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="success", server_default="success", nullable=False, index=True
    )
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Conversion id={self.id} user={self.telegram_id} "
            f"{self.source_kind}->{self.target_id}>"
        )
