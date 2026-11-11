"""Game statistics repository."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.game_stat import GameStat


class GameRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, telegram_id: int) -> GameStat:
        """Fetch the player's stat record, creating one if this is their first game."""
        stmt = (
            select(GameStat)
            .where(GameStat.telegram_id == telegram_id)
            .options(selectinload(GameStat.user))
        )
        stat = (await self.session.execute(stmt)).scalar_one_or_none()
        if stat is None:
            stat = GameStat(telegram_id=telegram_id, score=1000)
            self.session.add(stat)
            await self.session.flush()
            # Reload with user relation
            stat = (await self.session.execute(stmt)).scalar_one()
        return stat

    async def record_result(
        self,
        telegram_id: int,
        outcome: Literal["win", "loss", "draw"],
        score_delta: int,
    ) -> GameStat:
        """Update scores, streaks and counters after a finished match."""
        stat = await self.get_or_create(telegram_id)
        now = datetime.now(UTC)
        stat.last_played_at = now

        new_score = stat.score + score_delta
        # Lower bound score to 100 so nobody goes into negative oblivion
        stat.score = max(100, new_score)

        if outcome == "win":
            stat.wins += 1
            stat.current_streak += 1
            if stat.current_streak > stat.max_streak:
                stat.max_streak = stat.current_streak
        elif outcome == "loss":
            stat.losses += 1
            stat.current_streak = 0
        else:  # draw
            stat.draws += 1

        await self.session.flush()
        return stat

    async def get_leaderboard(self, limit: int = 10) -> Sequence[GameStat]:
        """Return the top players ordered by score, then wins."""
        stmt = (
            select(GameStat)
            .options(selectinload(GameStat.user))
            .order_by(desc(GameStat.score), desc(GameStat.wins), desc(GameStat.max_streak))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def get_user_rank(self, telegram_id: int) -> int:
        """Calculate the 1-based leaderboard rank of the given player."""
        stat = await self.get_or_create(telegram_id)
        # Count how many players have a higher score
        count_higher_stmt = (
            select(func.count(GameStat.telegram_id))
            .where(GameStat.score > stat.score)
        )
        higher_count = (await self.session.execute(count_higher_stmt)).scalar() or 0
        return higher_count + 1

    async def get_total_players(self) -> int:
        """Total number of players in the game database."""
        stmt = select(func.count(GameStat.telegram_id))
        count = (await self.session.execute(stmt)).scalar() or 0
        return count
