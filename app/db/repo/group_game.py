"""Leaderboards and ratings scoped to one group and one game type."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.group_game_stat import GroupGameStat


class GroupGameRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _key(self, chat_id: int, game_type: str, telegram_id: int):
        return (
            GroupGameStat.chat_id == chat_id,
            GroupGameStat.game_type == game_type,
            GroupGameStat.telegram_id == telegram_id,
        )

    async def get_or_create(
        self, chat_id: int, game_type: str, telegram_id: int
    ) -> GroupGameStat:
        stmt = select(GroupGameStat).where(*self._key(chat_id, game_type, telegram_id))
        stat = (await self.session.execute(stmt)).scalar_one_or_none()
        if stat is None:
            stat = GroupGameStat(
                chat_id=chat_id, game_type=game_type, telegram_id=telegram_id, score=1000
            )
            self.session.add(stat)
            await self.session.flush()
        return stat

    async def record_result(
        self,
        chat_id: int,
        game_type: str,
        telegram_id: int,
        outcome: Literal["win", "loss", "draw"],
        score_delta: int,
    ) -> GroupGameStat:
        stat = await self.get_or_create(chat_id, game_type, telegram_id)
        stat.last_played_at = datetime.now(UTC)
        stat.score = max(100, stat.score + score_delta)
        if outcome == "win":
            stat.wins += 1
            stat.current_streak += 1
            stat.max_streak = max(stat.max_streak, stat.current_streak)
        elif outcome == "loss":
            stat.losses += 1
            stat.current_streak = 0
        else:
            stat.draws += 1
        await self.session.flush()
        return stat

    async def leaderboard(
        self, chat_id: int, game_type: str, limit: int = 10
    ) -> Sequence[GroupGameStat]:
        stmt = (
            select(GroupGameStat)
            .where(
                GroupGameStat.chat_id == chat_id,
                GroupGameStat.game_type == game_type,
            )
            .order_by(
                desc(GroupGameStat.score),
                desc(GroupGameStat.wins),
                desc(GroupGameStat.max_streak),
            )
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def rank(self, chat_id: int, game_type: str, telegram_id: int) -> int:
        stat = await self.get_or_create(chat_id, game_type, telegram_id)
        higher = (
            await self.session.execute(
                select(func.count(GroupGameStat.telegram_id)).where(
                    GroupGameStat.chat_id == chat_id,
                    GroupGameStat.game_type == game_type,
                    GroupGameStat.score > stat.score,
                )
            )
        ).scalar()
        return int(higher or 0) + 1
