"""Aggregate rating across every game.

Each game keeps its *own* scoreboard (``game_stats`` for XO and one row per
``(chat_id, game_type)`` in ``group_game_stats``). The league, however, is
earned from the sum of all of them — playing different games must not reset
your progress.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.game_stat import GameStat
from app.db.models.group_game_stat import GroupGameStat


class TotalScoreRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _stmt(self):
        return union_all(
            select(GameStat.telegram_id.label("telegram_id"), GameStat.score.label("score")),
            select(
                GroupGameStat.telegram_id.label("telegram_id"),
                GroupGameStat.score.label("score"),
            ),
        )

    async def totals(self) -> dict[int, int]:
        """Return ``{telegram_id: combined score}`` over all games."""
        rows = (await self.session.execute(self._stmt())).all()
        out: dict[int, int] = defaultdict(int)
        for telegram_id, score in rows:
            out[int(telegram_id)] += int(score)
        return dict(out)

    async def total_for(self, telegram_id: int) -> int:
        return (await self.totals()).get(int(telegram_id), 0)

    async def top(self, limit: int = 10) -> list[tuple[int, int]]:
        totals = await self.totals()
        return sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:limit]

    async def rank_of(self, telegram_id: int) -> int:
        totals = await self.totals()
        mine = totals.get(telegram_id, 0)
        return sum(1 for score in totals.values() if score > mine) + 1

    async def for_ids(self, telegram_ids: Sequence[int]) -> dict[int, int]:
        wanted = set(int(i) for i in telegram_ids)
        totals = await self.totals()
        return {i: totals[i] for i in wanted if i in totals}
