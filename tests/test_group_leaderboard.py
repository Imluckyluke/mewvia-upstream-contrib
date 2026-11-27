"""Dedicated tests for the group-only, paginated games leaderboard."""

from __future__ import annotations

import pytest
from aiogram.types import InlineKeyboardMarkup, User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import GroupGameCB
from app.db.repo.game import GameRepo
from app.db.repo.group_game import GroupGameRepo
from app.db.repo.user import UserRepo
from app.features.games.group_leaderboard import PAGE_SIZE, render_leaderboard
from app.ui.emoji import EmojiRegistry

CHAT = -100777000111
OTHER_CHAT = -100777000222
EMPTY_CHAT = -100777000333

ALICE = 700001
BOB = 700002
CAROL = 700003
DAVE = 700004
EVE = 700005
FRANK = 700006


def _callbacks(markup: InlineKeyboardMarkup) -> list[GroupGameCB]:
    parsed: list[GroupGameCB] = []
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data:
                parsed.append(GroupGameCB.unpack(button.callback_data))
    return parsed


async def _add_user(
    session: AsyncSession, telegram_id: int, first_name: str, last_name: str = ""
) -> None:
    await UserRepo(session).upsert_from_telegram(
        TgUser(
            id=telegram_id,
            is_bot=False,
            first_name=first_name,
            last_name=last_name or None,
        )
    )


@pytest.mark.asyncio
async def test_combined_leaderboard_uses_one_baseline_and_stays_in_group(
    session: AsyncSession,
) -> None:
    repo = GroupGameRepo(session)

    # Alice plays three different games: the baseline must be counted once.
    await repo.record_result(CHAT, "xo", ALICE, "win", 25)
    await repo.record_result(CHAT, "gol", ALICE, "win", 40)
    await repo.record_result(CHAT, "rps", ALICE, "loss", -10)
    # Bob only plays XO; Carol and Dave tie on the combined metric.
    await repo.record_result(CHAT, "xo", BOB, "loss", -10)
    await repo.record_result(CHAT, "gol", CAROL, "draw", 0)
    await repo.record_result(CHAT, "rps", DAVE, "draw", 0)
    # Another group must never leak into this ladder.
    await repo.record_result(OTHER_CHAT, "xo", ALICE, "win", 500)

    rows = await repo.combined_leaderboard(CHAT)

    assert [(row.telegram_id, row.score, row.wins, row.losses, row.draws) for row in rows] == [
        (ALICE, 1055, 2, 1, 0),
        (CAROL, 1000, 0, 0, 1),
        (DAVE, 1000, 0, 0, 1),
        (BOB, 990, 0, 1, 0),
    ]
    assert await repo.combined_total_players(CHAT) == 4

    other = await repo.combined_leaderboard(OTHER_CHAT)
    assert [(row.telegram_id, row.score) for row in other] == [(ALICE, 1500)]


@pytest.mark.asyncio
async def test_per_game_pages_reach_all_players_with_stable_order(
    session: AsyncSession,
) -> None:
    repo = GroupGameRepo(session)
    ids = [800000 + index for index in range(PAGE_SIZE + 2)]
    for index, telegram_id in enumerate(ids):
        # Distinct scores keep the order obvious; wins are the second key.
        await repo.record_result(CHAT, "xo", telegram_id, "win", 50 - index)

    first = await repo.leaderboard(CHAT, "xo", limit=5, offset=0)
    second = await repo.leaderboard(CHAT, "xo", limit=5, offset=5)
    third = await repo.leaderboard(CHAT, "xo", limit=5, offset=10)

    assert [row.telegram_id for row in first] == ids[:5]
    assert [row.telegram_id for row in second] == ids[5:10]
    assert [row.telegram_id for row in third] == ids[10:]
    assert [row.score for row in first] == [1050, 1049, 1048, 1047, 1046]
    assert await repo.total_players(CHAT, "xo") == len(ids)

    # Equal scores fall back to the lower Telegram id, never to DB row order.
    tied = [900002, 900001]
    for telegram_id in tied:
        await repo.record_result(CHAT, "gol", telegram_id, "draw", 0)
    tied_rows = await repo.leaderboard(CHAT, "gol", limit=10, offset=0)
    assert [row.telegram_id for row in tied_rows] == [900001, 900002]


@pytest.mark.asyncio
async def test_combined_pagination_reaches_all_players(session: AsyncSession) -> None:
    repo = GroupGameRepo(session)
    ids = [820000 + index for index in range(PAGE_SIZE + 1)]
    for index, telegram_id in enumerate(ids):
        # Alternate games so the combined query has to merge both ladders.
        await repo.record_result(CHAT, "gol" if index % 2 else "xo", telegram_id, "win", 30 - index)

    first = await repo.combined_leaderboard(CHAT, limit=PAGE_SIZE, offset=0)
    second = await repo.combined_leaderboard(CHAT, limit=PAGE_SIZE, offset=PAGE_SIZE)
    assert [row.telegram_id for row in first] == ids[:PAGE_SIZE]
    assert [row.telegram_id for row in second] == ids[PAGE_SIZE:]
    assert await repo.combined_total_players(CHAT) == len(ids)

    text, markup = await render_leaderboard(session, CHAT, "all", 1, EmojiRegistry())
    assert "صفحه <b>۲</b> از <b>۲</b>" in text
    assert any(cb.action == "lb" and cb.game == "all" and cb.cell == 0 for cb in _callbacks(markup))


@pytest.mark.asyncio
async def test_render_combined_leaderboard_explains_metric_and_has_tabs(
    session: AsyncSession,
) -> None:
    await _add_user(session, ALICE, "علی", "احمدی")
    await _add_user(session, BOB, "مریم", "کریمی")
    await _add_user(session, EVE, "کاربر", "گروه دیگر")
    await _add_user(session, FRANK, "فرانک", "جهانی")

    repo = GroupGameRepo(session)
    await repo.record_result(CHAT, "xo", ALICE, "win", 25)
    await repo.record_result(CHAT, "gol", ALICE, "win", 30)
    await repo.record_result(CHAT, "xo", BOB, "loss", -10)
    await repo.record_result(OTHER_CHAT, "xo", EVE, "win", 900)
    # A global (private-chat) rating must not leak into a group ladder.
    await GameRepo(session).record_result(FRANK, "win", 900)

    registry = EmojiRegistry()
    text, markup = await render_leaderboard(session, CHAT, "all", 0, registry)

    assert "همهٔ بازی‌ها" in text
    assert "امتیاز ترکیبی" in text
    assert "۱۰۰۰" in text and "تغییر امتیاز هر بازی" in text
    assert "برد" in text and "باخت" in text and "مساوی" in text
    assert "علی احمدی" in text
    assert "مریم کریمی" in text
    assert "۱۰۵۵" in text  # 1000 + 25 + 30
    assert "گروه دیگر" not in text
    assert "فرانک" not in text and "۱۹۰۰" not in text
    assert "صفحه <b>۱</b> از <b>۱</b>" in text

    parsed = _callbacks(markup)
    tabs = {(cb.action, cb.game) for cb in parsed}
    assert ("lb", "all") in tabs
    assert ("lb", "xo") in tabs
    assert ("lb", "gol") in tabs
    assert ("lb", "rps") in tabs
    assert ("lb", "four") in tabs
    assert ("menu", "all") in tabs


@pytest.mark.asyncio
async def test_render_per_game_pagination_and_page_clamping(
    session: AsyncSession,
) -> None:
    repo = GroupGameRepo(session)
    ids = [810000 + index for index in range(PAGE_SIZE + 2)]
    for index, telegram_id in enumerate(ids):
        await _add_user(session, telegram_id, f"بازیکن {index + 1}")
        await repo.record_result(CHAT, "xo", telegram_id, "win", 50 - index)
    # A high score in another group must not appear on any page.
    await _add_user(session, EVE, "نفوذی")
    await repo.record_result(OTHER_CHAT, "xo", EVE, "win", 500)

    registry = EmojiRegistry()

    page_one, kb_one = await render_leaderboard(session, CHAT, "xo", 0, registry)
    assert "صفحه <b>۱</b> از <b>۲</b>" in page_one
    assert "بازیکن 1" in page_one
    assert "نفوذی" not in page_one
    page_one_cbs = _callbacks(kb_one)
    assert any(cb.action == "lb" and cb.game == "xo" and cb.cell == 1 for cb in page_one_cbs)
    assert not any(cb.action == "lb" and cb.cell == -1 for cb in page_one_cbs)

    # Old panels omit `cell`, leaving the callback default at -1; that is page 0.
    default_page, _ = await render_leaderboard(session, CHAT, "xo", -1, registry)
    assert default_page == page_one

    page_two, kb_two = await render_leaderboard(session, CHAT, "xo", 1, registry)
    assert "صفحه <b>۲</b> از <b>۲</b>" in page_two
    # Global ranks continue across pages: 11 and 12, not 1 and 2.
    assert "۱۱." in page_two and "۱۲." in page_two
    assert "بازیکن 11" in page_two and "بازیکن 12" in page_two
    page_two_cbs = _callbacks(kb_two)
    assert any(cb.action == "lb" and cb.game == "xo" and cb.cell == 0 for cb in page_two_cbs)
    assert not any(cb.action == "lb" and cb.cell == 2 for cb in page_two_cbs)

    # A stale "next" callback with an out-of-range page clamps to the last page.
    clamped, kb_clamped = await render_leaderboard(session, CHAT, "xo", 99, registry)
    assert clamped == page_two
    assert any(
        cb.action == "lb" and cb.game == "xo" and cb.cell == 0 for cb in _callbacks(kb_clamped)
    )
    assert any(cb.action == "menu" and cb.game == "all" for cb in _callbacks(kb_clamped))


@pytest.mark.asyncio
async def test_unknown_game_defaults_to_combined_and_empty_group_is_safe(
    session: AsyncSession,
) -> None:
    registry = EmojiRegistry()

    text, markup = await render_leaderboard(session, EMPTY_CHAT, "not-a-game", 0, registry)
    assert "همهٔ بازی‌ها" in text
    assert "امتیاز ترکیبی" in text
    assert "هنوز" in text
    assert "صفحه <b>۱</b> از <b>۱</b>" in text
    assert "۰ بازیکن" in text

    parsed = _callbacks(markup)
    assert any(cb.action == "menu" and cb.game == "all" for cb in parsed)
    assert {cb.game for cb in parsed if cb.action == "lb"} == {"all", "xo", "gol", "rps", "four"}

    xo_text, _ = await render_leaderboard(session, EMPTY_CHAT, "xo", 0, registry)
    assert "هنوز بازی «" in xo_text
    assert "همه بازی‌ها" not in xo_text
