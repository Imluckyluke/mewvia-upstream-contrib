"""Tests for group multiplayer games (lobby, scoring and leaderboards)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import GroupGameCB
from app.db.repo.group_game import GroupGameRepo
from app.features.games.group_router import _render_lobby
from app.ui.emoji import EmojiRegistry

CHAT_ID = -1001234567890
ALICE = 555000001
BOB = 555000002


def test_group_callback_roundtrip() -> None:
    cb = GroupGameCB(action="join", game="xo")
    packed = cb.pack()
    parsed = GroupGameCB.unpack(packed)
    assert parsed.action == "join"
    assert parsed.game == "xo"
    move = GroupGameCB(action="move", game="xo", cell=4).pack()
    assert GroupGameCB.unpack(move).cell == 4


def test_lobby_shows_both_player_names() -> None:
    import asyncio

    async def run() -> None:
        lobby = {
            "game": "xo",
            "status": "lobby",
            "host": ALICE,
            "players": [
                {"id": ALICE, "name": "علی احمدی"},
                {"id": BOB, "name": "مریم کریمی"},
            ],
        }

        class FakeSession:
            pass

        text, markup = await _render_lobby(FakeSession(), lobby, EmojiRegistry())  # type: ignore[arg-type]
        assert "علی احمدی" in text
        assert "مریم کریمی" in text
        labels = [button.text for row in markup.inline_keyboard for button in row]
        assert any("شروع بازی" in label for label in labels)
        assert not any("ورود به بازی" in label for label in labels)

    asyncio.run(run())


@pytest.mark.asyncio
async def test_group_repo_scores_and_leaderboard(session: AsyncSession) -> None:
    repo = GroupGameRepo(session)
    await repo.record_result(CHAT_ID, "xo", ALICE, "win", 25)
    await repo.record_result(CHAT_ID, "xo", BOB, "loss", -10)
    # A different game must keep its own ladder.
    await repo.record_result(CHAT_ID, "other", BOB, "win", 40)

    xo_board = await repo.leaderboard(CHAT_ID, "xo")
    assert [row.telegram_id for row in xo_board] == [ALICE, BOB]
    assert xo_board[0].score == 1025
    assert xo_board[1].score == 990

    other_board = await repo.leaderboard(CHAT_ID, "other")
    assert len(other_board) == 1
    assert other_board[0].telegram_id == BOB
    assert other_board[0].score == 1040

    assert await repo.rank(CHAT_ID, "xo", ALICE) == 1
    assert await repo.rank(CHAT_ID, "xo", BOB) == 2


@pytest.mark.asyncio
async def test_two_player_group_flow(
    session: AsyncSession, app_dispatcher: object
) -> None:
    """Host opens a lobby, player 2 joins, host starts, both moves score."""
    from unittest.mock import AsyncMock, MagicMock

    from aiogram.enums import ChatType
    from aiogram.types import Chat, Message, User as TgUser

    from app.callbacks import GroupGameCB
    from app.db.repo.game import GameRepo
    from app.db.repo.user import UserRepo
    from app.features.games import group_session
    from app.features.games.group_router import cb_join, cb_move, cb_start

    chat = Chat(id=CHAT_ID, type=ChatType.SUPERGROUP)
    host_user = TgUser(id=ALICE, is_bot=False, first_name="علی", last_name="احمدی")
    guest_user = TgUser(id=BOB, is_bot=False, first_name="مریم", last_name="کریمی")

    def fake_message() -> MagicMock:
        # spec makes isinstance(mock, Message) true, which the handlers check.
        m = MagicMock(spec=Message)
        m.chat = chat
        m.message_id = 101
        m.edit_text = AsyncMock()
        return m

    class FakeQuery:
        def __init__(self, user: TgUser, message: MagicMock) -> None:
            self.from_user = user
            self.message = message

        async def answer(self, *args: object, **kwargs: object) -> None:
            return None

    await UserRepo(session).upsert_from_telegram(host_user)
    await UserRepo(session).upsert_from_telegram(guest_user)
    await group_session.drop(CHAT_ID)
    await group_session.save(
        CHAT_ID,
        {
            "game": "xo",
            "status": "lobby",
            "host": ALICE,
            "players": [{"id": ALICE, "name": host_user.full_name}],
        },
    )
    msg = fake_message()

    # Player 2 joins — their name must appear in the preview.
    await cb_join(FakeQuery(guest_user, msg), session)  # type: ignore[arg-type]
    state = await group_session.load(CHAT_ID)
    assert state is not None and len(state["players"]) == 2
    assert state["players"][1]["name"] == guest_user.full_name

    # Only the host can start.
    await cb_start(FakeQuery(guest_user, msg), session)  # type: ignore[arg-type]
    state = await group_session.load(CHAT_ID)
    assert state is not None and state["status"] == "lobby"

    await cb_start(FakeQuery(host_user, msg), session)  # type: ignore[arg-type]
    state = await group_session.load(CHAT_ID)
    assert state is not None and state["status"] == "playing"

    # Host plays first, then guest — the turn guard must alternate.
    await cb_move(
        FakeQuery(guest_user, msg),  # type: ignore[arg-type]
        GroupGameCB(action="move", game="xo", cell=0),
        session,
    )
    state = await group_session.load(CHAT_ID)
    assert state is not None and state["board"][0] == "_"

    await cb_move(
        FakeQuery(host_user, msg),  # type: ignore[arg-type]
        GroupGameCB(action="move", game="xo", cell=0),
        session,
    )
    state = await group_session.load(CHAT_ID)
    assert state is not None and state["board"][0] == "X"

    # Ratings are written in both the global and per-group tables.
    global_stat = await GameRepo(session).get_or_create(ALICE)
    group_stat = await GroupGameRepo(session).get_or_create(CHAT_ID, "xo", ALICE)
    assert global_stat.score >= 1000 and group_stat.score >= 1000
    await UserRepo(session).upsert_from_telegram(host_user)
    await UserRepo(session).upsert_from_telegram(guest_user)
    await group_session.drop(CHAT_ID)


def test_group_command_menu_includes_games() -> None:
    from app.__main__ import GROUP_BOT_COMMANDS

    names = {cmd.command for cmd in GROUP_BOT_COMMANDS}
    assert "game" in names
    assert "game_lb" in names


@pytest.mark.asyncio
async def test_group_game_panel_blocks_other_owners_except_join_and_move() -> None:
    """A lobby opened by one user is locked to them; join stays public."""
    from unittest.mock import AsyncMock, MagicMock

    from aiogram.enums import ChatType
    from aiogram.types import CallbackQuery, Chat, Message, User as TgUser

    from app.callbacks import GroupGameCB
    from app.middlewares.group_guard import GroupPanelGuardMiddleware, store_panel

    chat = Chat(id=CHAT_ID, type=ChatType.SUPERGROUP)
    opened_by = TgUser(id=ALICE, is_bot=False, first_name="علی")
    other = TgUser(id=BOB, is_bot=False, first_name="مریم")

    msg = MagicMock(spec=Message)
    msg.chat = chat
    msg.message_id = 42

    await store_panel(CHAT_ID, 42, ALICE, "علی", kind="group_game")
    guard = GroupPanelGuardMiddleware()

    async def handler(event: object, data: object) -> str:
        return "passed"

    def make_query(user: TgUser, action: str) -> CallbackQuery:
        q = MagicMock(spec=CallbackQuery)
        q.from_user = user
        q.message = msg
        q.data = GroupGameCB(action=action, game="xo").pack()
        q.answer = AsyncMock()
        return q

    for action in ("start", "cancel", "lb", "lobby"):
        result = await guard(handler, make_query(other, action), {})  # type: ignore[arg-type]
        assert result is None, f"{action} must be blocked for non-owner"

    for action in ("join", "move"):
        result = await guard(handler, make_query(other, action), {})  # type: ignore[arg-type]
        assert result == "passed", f"{action} must stay reachable"

    result = await guard(handler, make_query(opened_by, "start"), {})  # type: ignore[arg-type]
    assert result == "passed"


def test_games_category_contains_only_game_slots() -> None:
    from app.features.uidemo import CATEGORIES, _category_slots
    from app.db.seed import EMOJI_SLOTS

    games = next(c for c in CATEGORIES if c[0] == "games")
    ui = next(c for c in CATEGORIES if c[0] == "ui")
    assert games[2:] == (400, 419)
    assert ui[2] == 420

    class Slot:
        def __init__(self, name: str, order: int) -> None:
            self.slot = name
            self.sort_order = order

    rows = [Slot(s.slot, s.sort_order) for s in EMOJI_SLOTS]
    names = [s.slot for s in _category_slots(rows, "games")]
    assert "xo_x" in names and "xo_o" in names and "trophy" in names
    assert "handshake" in names and "bot_robot" in names
    # Generic UI icons must not pollute the games category.
    assert "calendar" not in names and "reddit" not in names and "settings" not in names
