"""Per-group, per-game player statistics.

Global ratings stay in ``game_stats`` (one row per player). This table keeps a
separate ladder for each game type inside each group, so a new game can be
added later without touching the schema or mixing its scores with XO's.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GroupGameStat(Base):
    __tablename__ = "group_game_stats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    game_type: Mapped[str] = mapped_column(
        String(32), primary_key=True, default="xo", server_default="xo"
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)

    score: Mapped[int] = mapped_column(
        Integer, default=1000, server_default="1000", nullable=False, index=True
    )
    wins: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    losses: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    draws: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    current_streak: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    max_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def total_games(self) -> int:
        return self.wins + self.losses + self.draws

    @property
    def win_rate(self) -> float:
        if self.total_games == 0:
            return 0.0
        return round((self.wins / self.total_games) * 100, 1)
