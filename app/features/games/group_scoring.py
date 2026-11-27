"""Settle both players' ratings in one database transaction."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.group_game_result import GroupGameResult
from app.db.repo.group_game import GroupGameRepo

Outcome = Literal["win", "loss", "draw"]


def rating_delta(outcome: Outcome, score: int, streak: int) -> int:
    if outcome == "win":
        return 25 + min(10, streak * 2)
    if outcome == "loss":
        return -min(10, max(0, score - 100))
    return 0  # Repeated draws should not earn ladder points.


async def settle_match(
    session: AsyncSession,
    chat_id: int,
    game: str,
    match_id: str,
    players: list[int],
    winner: int | None,
) -> dict[str, list[int]]:
    # Claim the match inside the same transaction as its rating changes. If a process
    # stops after commit but before updating the live panel, a retry reuses the receipt.
    claimed = await session.scalar(
        insert(GroupGameResult)
        .values(match_id=match_id, rating_changes={})
        .on_conflict_do_nothing()
        .returning(GroupGameResult.match_id)
    )
    if claimed is None:
        previous = await session.execute(
            select(GroupGameResult.rating_changes).where(GroupGameResult.match_id == match_id)
        )
        return previous.scalar_one()

    group_repo = GroupGameRepo(session)
    deltas, ratings = [0, 0], [0, 0]
    # The umbrella league already reads this table. Writing to the solo XO table
    # would count the same match twice and put Gol/RPS wins on the XO board.
    # Competing matches can share players. Lock ratings in the same order
    # regardless of who hosted, and calculate each delta after acquiring its lock.
    for index, pid in sorted(enumerate(players), key=lambda pair: pair[1]):
        outcome: Outcome = "draw" if winner is None else "win" if winner == index else "loss"
        stat = await group_repo.get_or_create(chat_id, game, pid, lock=True)
        before = stat.score
        stat = await group_repo.record_result(
            chat_id, game, pid, outcome, rating_delta(outcome, before, stat.current_streak)
        )
        deltas[index], ratings[index] = stat.score - before, stat.score

    result = {"deltas": deltas, "ratings": ratings}
    await session.execute(
        update(GroupGameResult)
        .where(GroupGameResult.match_id == match_id)
        .values(rating_changes=result)
    )
    return result
