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
