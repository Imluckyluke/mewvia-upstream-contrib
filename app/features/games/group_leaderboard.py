"""Paginated leaderboards for multiplayer games inside one Telegram group.

Every number on this screen comes from ``group_game_stats`` and is filtered by
``chat_id``; global ratings are deliberately never read here. The default tab
combines all of a group's game ladders without multiplying the 1000-point
baseline, while the XO / Gol / RPS tabs show one ladder at a time.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import GroupGameCB
from app.core.units import fa_digits
from app.db.models.group_game_stat import GroupGameStat
from app.db.models.user import User
from app.db.repo.group_game import CombinedGroupStat, GroupGameRepo
from app.features.games.group_rules import GAME_LABELS
from app.ui.emoji import EmojiRegistry
from app.ui.kb import KB
from app.ui.styles import Style

PAGE_SIZE = 10
ALL_GAMES = "all"
TAB_LABELS = {ALL_GAMES: "همهٔ بازی‌ها", **GAME_LABELS}


def _page_count(total: int) -> int:
    return max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)


def _clamp_page(page: int, pages: int) -> int:
    return min(max(page, 0), pages - 1)


async def _display_names(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = (await session.execute(select(User).where(User.telegram_id.in_(ids)))).scalars().all()
    return {user.telegram_id: user.full_name for user in rows}


def _empty_text(game: str) -> str:
    if game == ALL_GAMES:
        return "<i>هنوز بازی‌ای در این گروه ثبت نشده است.</i>"
    label = html.escape(TAB_LABELS[game])
    return f"<i>هنوز بازی «{label}» در این گروه ثبت نشده است.</i>"


async def render_leaderboard(
    session: AsyncSession,
    chat_id: int,
    game: str,
    page: int,
    registry: EmojiRegistry,
) -> tuple[str, InlineKeyboardMarkup]:
    """Render one page of a group-only leaderboard.

    ``game`` is ``all`` (combined) or one of ``xo`` / ``gol`` / ``rps``;
    unknown values fall back to the combined view. ``page`` is zero-based and
    is clamped to the last page so a stale button can never show a blank screen.
    """
    game = game if game in TAB_LABELS else ALL_GAMES
    repo = GroupGameRepo(session)
    rows: Sequence[GroupGameStat | CombinedGroupStat]

    if game == ALL_GAMES:
        total = await repo.combined_total_players(chat_id)
        pages = _page_count(total)
        page = _clamp_page(int(page), pages)
        rows = await repo.combined_leaderboard(chat_id, limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    else:
        total = await repo.total_players(chat_id, game)
        pages = _page_count(total)
        page = _clamp_page(int(page), pages)
        rows = await repo.leaderboard(chat_id, game, limit=PAGE_SIZE, offset=page * PAGE_SIZE)

    lines = [f"{registry.text('trophy')} <b>جدول گروهی — {TAB_LABELS[game]}</b>", ""]
    if game == ALL_GAMES:
        lines.append(
            "<i>امتیاز ترکیبی = ۱۰۰۰ + جمع اختلاف امتیاز هر بازی با ۱۰۰۰؛ "
            "هر بازی از ۱۰۰۰ شروع می‌شود، پس بازی‌های بیشتر پایه را چند برابر نمی‌کنند.</i>"
        )
        lines.append("")

    if not rows:
        lines.append(_empty_text(game))
    else:
        names = await _display_names(session, [row.telegram_id for row in rows])
        medals = ["🥇", "🥈", "🥉"]
        rank = page * PAGE_SIZE
        for row in rows:
            rank += 1
            badge = medals[rank - 1] if rank <= 3 else f"<b>{fa_digits(str(rank))}.</b>"
            name = html.escape(names.get(row.telegram_id, "بازیکن")[:100])
            lines.append(
                f"{badge} <b>{name}</b> — <b>{fa_digits(str(row.score))}</b> امتیاز\n"
                f"برد: {fa_digits(str(row.wins))} · باخت: {fa_digits(str(row.losses))}"
                f" · مساوی: {fa_digits(str(row.draws))}"
            )

    lines.append("")
    lines.append(
        f"صفحه <b>{fa_digits(str(page + 1))}</b> از "
        f"<b>{fa_digits(str(pages))}</b> — "
        f"{fa_digits(str(total))} بازیکن"
    )

    kb = KB(registry)
    for tab, label in TAB_LABELS.items():
        kb.button(
            label,
            callback_data=GroupGameCB(action="lb", game=tab, cell=0).pack(),
            style=Style.PRIMARY if tab == game else Style.NEUTRAL,
            emoji_slot="trophy" if tab == ALL_GAMES else "game",
        )
    if page > 0:
        kb.button(
            "صفحه قبل",
            callback_data=GroupGameCB(action="lb", game=game, cell=page - 1).pack(),
            style=Style.NEUTRAL,
            emoji_slot="back",
        )
    if page < pages - 1:
        kb.button(
            "صفحه بعد",
            callback_data=GroupGameCB(action="lb", game=game, cell=page + 1).pack(),
            style=Style.PRIMARY,
            emoji_slot="next",
        )
    kb.button(
        "فهرست بازی‌ها",
        callback_data=GroupGameCB(action="menu", game=ALL_GAMES).pack(),
        style=Style.NEUTRAL,
        emoji_slot="back",
    )
    navigation_count = int(page > 0) + int(page < pages - 1)
    kb.adjust(2, 2, *([navigation_count] if navigation_count else []), 1)
    return "\n".join(lines), kb.as_markup()
