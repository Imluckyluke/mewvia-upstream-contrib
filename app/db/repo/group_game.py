"""Leaderboards and ratings scoped to one group and one game type."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.group_game_stat import GroupGameStat


@dataclass(frozen=True, slots=True)
class CombinedGroupStat:
    """One player's totals across every game ladder in a single group."""

    telegram_id: int
    score: int
    wins: int
    losses: int
    draws: int


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
        self, chat_id: int, game_type: str, telegram_id: int, *, lock: bool = False
    ) -> GroupGameStat:
        stmt = select(GroupGameStat).where(*self._key(chat_id, game_type, telegram_id))
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        stat = (await self.session.execute(stmt)).scalar_one_or_none()
        if stat is None:
            await self.session.execute(
                insert(GroupGameStat)
                .values(chat_id=chat_id, game_type=game_type, telegram_id=telegram_id)
                .on_conflict_do_nothing()
            )
            stat = (await self.session.execute(stmt)).scalar_one()
        return stat

    async def record_result(
        self,
        chat_id: int,
        game_type: str,
        telegram_id: int,
        outcome: Literal["win", "loss", "draw"],
        score_delta: int,
    ) -> GroupGameStat:
        stat = await self.get_or_create(chat_id, game_type, telegram_id, lock=True)
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
        self,
        chat_id: int,
        game_type: str,
        limit: int = 10,
        offset: int = 0,
    ) -> Sequence[GroupGameStat]:
        """One page of a single game's ladder, with a deterministic tie-break."""
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
                GroupGameStat.telegram_id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def combined_leaderboard(
        self,
        chat_id: int,
        limit: int = 10,
        offset: int = 0,
    ) -> Sequence[CombinedGroupStat]:
        """Group ladder across every game.

        Each game starts at 1000, so simply summing the ladders would multiply
        that baseline by the number of games played. The combined rating instead
        keeps one baseline and adds only each ladder's movement from it:
        ``1000 + sum(score - 1000)``.
        """
        combined_score = (1000 + func.sum(GroupGameStat.score - 1000)).label("combined_score")
        total_wins = func.sum(GroupGameStat.wins).label("total_wins")
        total_losses = func.sum(GroupGameStat.losses).label("total_losses")
        total_draws = func.sum(GroupGameStat.draws).label("total_draws")
        stmt = (
            select(
                GroupGameStat.telegram_id,
                combined_score,
                total_wins,
                total_losses,
                total_draws,
            )
            .where(GroupGameStat.chat_id == chat_id)
            .group_by(GroupGameStat.telegram_id)
            .order_by(
                combined_score.desc(),
                total_wins.desc(),
                GroupGameStat.telegram_id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            CombinedGroupStat(
                telegram_id=int(row.telegram_id),
                score=int(row.combined_score),
                wins=int(row.total_wins),
                losses=int(row.total_losses),
                draws=int(row.total_draws),
            )
            for row in rows
        ]

    async def combined_total_players(self, chat_id: int) -> int:
        stmt = select(func.count(func.distinct(GroupGameStat.telegram_id))).where(
            GroupGameStat.chat_id == chat_id
        )
        return int((await self.session.execute(stmt)).scalar() or 0)

    async def total_players(self, chat_id: int, game_type: str) -> int:
        stmt = select(func.count(GroupGameStat.telegram_id)).where(
            GroupGameStat.chat_id == chat_id,
            GroupGameStat.game_type == game_type,
        )
        return int((await self.session.execute(stmt)).scalar() or 0)

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
