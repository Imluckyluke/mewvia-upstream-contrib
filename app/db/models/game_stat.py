"""Player game statistics and league rating records."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class GameStat(Base):
    __tablename__ = "game_stats"

    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )

    #: Elo/Rating score (starts at 1000)
    score: Mapped[int] = mapped_column(
        Integer, default=1000, server_default="1000", nullable=False, index=True
    )

    wins: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    losses: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    draws: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    current_streak: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    max_streak: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    last_played_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship("User", lazy="joined")  # type: ignore[name-defined] # noqa: F821

    @property
    def total_games(self) -> int:
        return self.wins + self.losses + self.draws

    @property
    def win_rate(self) -> float:
        if self.total_games == 0:
            return 0.0
        return round((self.wins / self.total_games) * 100, 1)
