"""Group gameplay, authorization and durable scoring regressions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from importlib import import_module
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Dispatcher
from aiogram.enums import ChatType
from aiogram.methods import GetMe, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import GroupGameCB
from app.db.repo.game import GameRepo
from app.db.repo.group_game import GroupGameRepo
from app.features.games import group_session
from app.features.games.group_rules import GOL_ROUNDS, RPS_LABELS, pack_action, rps_winner
from app.features.games.group_scoring import rating_delta
from app.middlewares.group_guard import GroupPanelGuardMiddleware
from app.ui.emoji import EmojiRegistry
from tests.test_phase1_middleware import FakeBot

CHAT_ID = -1001234567890
group_router = import_module("app.features.games.group_router")


@pytest.fixture
async def group(monkeypatch: pytest.MonkeyPatch, session: AsyncSession):
    actors = [
        User(id=index, first_name=name, is_bot=False)
        for index, name in enumerate(("علی <A>", "مریم", "تماشاگر"), start=945_000)
    ]
    chat = Chat(id=CHAT_ID, type=ChatType.SUPERGROUP)
    message = MagicMock(spec=Message, chat=chat, message_id=34)
    message.edit_text = AsyncMock()
    message.answer = AsyncMock(return_value=message)
    message.get_url.return_value = "https://t.me/c/1234567890/34"
    monkeypatch.setattr(group_router, "_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(group_router, "_registry", AsyncMock(return_value=EmojiRegistry()))
    await group_session.drop(CHAT_ID)

    async def tap(
        player: int,
        action: str,
        *,
        game: str = "xo",
        message_id: int = 34,
        cb: GroupGameCB | None = None,
        **kwargs,
    ) -> CallbackQuery:
        state = await group_session.load(CHAT_ID)
        if cb is None:
            cb = (
                GroupGameCB.unpack(pack_action(state, action, **kwargs))
                if state and action not in {"create", "resume", "lb", "menu"}
                else GroupGameCB(action=action, game=game, **kwargs)
            )
        target = (
            message
            if message_id == 34
            else MagicMock(spec=Message, chat=chat, message_id=message_id, edit_text=AsyncMock())
        )
        query = MagicMock(
            spec=CallbackQuery, from_user=actors[player], message=target, answer=AsyncMock()
        )
        await group_router.cb_group_game(query, cb, session)
        return query

    yield tap, actors, message
    await group_session.drop(CHAT_ID)


async def active_match(tap, game: str) -> None:
    await tap(0, "create", game=game)
    await tap(1, "join")
    await tap(0, "start")


async def test_xo_permissions_stale_callback_and_scoring(group, session: AsyncSession) -> None:
    tap, (host, guest, _spectator), message = group
    await tap(0, "create")
    assert "علی &lt;A&gt;" in message.answer.await_args.args[0]
    await tap(1, "join")
    assert "مریم" in message.edit_text.await_args.args[0]
    await tap(2, "cancel")
    await tap(1, "start")
    assert (await group_session.load(CHAT_ID))["status"] == "lobby"
    await tap(0, "start")
    playing = await group_session.load(CHAT_ID)
    stale = GroupGameCB.unpack(pack_action(playing, "move", cell=8))
    await tap(0, "move", cell=0, message_id=99)
    await tap(1, "move", cell=0)
    assert (await group_session.load(CHAT_ID))["board"] == "_________"
    await tap(0, "move", cell=0)
    await tap(1, "move", cb=stale)
    assert (await group_session.load(CHAT_ID))["board"] == "X________"
    for player, cell in [(1, 3), (0, 1), (1, 4)]:
        await tap(player, "move", cell=cell)
    before = await group_session.load(CHAT_ID)
    final_move = GroupGameCB.unpack(pack_action(before, "move", cell=2))
    await asyncio.gather(tap(0, "move", cb=final_move), tap(0, "move", cb=final_move))
    final = await group_session.load(CHAT_ID)
    assert final["status"] == "ended"
    assert final["winning_cells"] == [0, 1, 2]
    assert final["deltas"] == [25, -10]
    # Simulate a crash after the DB commit but before the cache write.
    pending = {**final, "status": "settling", "revision": before["revision"]}
    pending.pop("ratings")
    pending.pop("deltas")
    await group_session.save(CHAT_ID, pending)
    # Even a different cell must replay the already-decided result.
    await tap(0, "move", cell=8)
    assert (await group_session.load(CHAT_ID))["ratings"] == [1025, 990]
    assert (await GameRepo(session).get_or_create(host.id)).wins == 0
    assert (await GroupGameRepo(session).get_or_create(CHAT_ID, "xo", host.id)).wins == 1
    stat = await GroupGameRepo(session).get_or_create(CHAT_ID, "xo", guest.id)
    assert stat.losses == 1 and stat.score == 990


async def test_gol_hides_choices_resumes_and_swaps_roles(group) -> None:
    tap, _actors, message = group
    await active_match(tap, "gol")
    for hand in range(GOL_ROUNDS):
        hider = hand % 2
        await tap(1 - hider, "gol_hide", side="right")
        assert (await group_session.load(CHAT_ID))["phase"] == "hide"
        await tap(hider, "gol_hide", side="left")
        assert (await group_session.load(CHAT_ID))["phase"] == "guess"
        panel = message.edit_text.await_args.args[0]
        await tap(hider, "resume", game="gol")
        assert message.answer.await_args.args[0] == panel
        assert "دست چپ" not in panel
        await tap(hider, "gol_guess", side="left")
        assert (await group_session.load(CHAT_ID))["phase"] == "guess"
        await tap(1 - hider, "gol_guess", side="left")
        if hand < GOL_ROUNDS - 1:
            await tap(2, "next")
            assert (await group_session.load(CHAT_ID))["round"] == hand + 1
            await tap(1 - hider, "next")

    final = await group_session.load(CHAT_ID)
    assert final["status"] == "ended"
    assert final["points"] == [4, 4]
    assert final["winner"] is None and final["deltas"] == [0, 0]


async def test_rps_secret_commit_and_public_ranking_panel(group) -> None:
    tap, _actors, message = group
    await active_match(tap, "rps")
    initial = await group_session.load(CHAT_ID)
    p2 = GroupGameCB.unpack(pack_action(initial, "rps_pick", side="scissors"))
    edits = message.edit_text.await_count
    await tap(0, "rps_pick", side="rock")
    await tap(0, "rps_pick", side="paper")
    assert message.edit_text.await_count == edits
    await tap(1, "rps_pick", cb=p2)
    final = await group_session.load(CHAT_ID)
    assert final["status"] == "ended" and final["winner"] == 0
    result_edits = message.edit_text.await_count
    assert result_edits == edits + 1
    message.answer = AsyncMock()
    await tap(2, "lb", cb=GroupGameCB.unpack(pack_action(final, "lb", cell=0)))
    message.answer.assert_awaited_once()
    assert message.edit_text.await_count == result_edits


async def test_cancel_is_unrated_and_old_lobby_cannot_start_new_game(group, session) -> None:
    tap, _actors, _message = group
    await active_match(tap, "xo")
    state = await group_session.load(CHAT_ID)
    old = GroupGameCB.unpack(pack_action(state, "move", cell=0))
    await tap(1, "cancel")
    assert await GroupGameRepo(session).total_players(CHAT_ID, "xo") == 0
    await active_match(tap, "xo")
    await tap(0, "move", cb=old)
    assert (await group_session.load(CHAT_ID))["board"] == "_________"


async def test_xo_tie_and_player_only_rematch(group, session: AsyncSession) -> None:
    tap, actors, _message = group
    await active_match(tap, "xo")
    for turn, cell in enumerate([0, 1, 2, 4, 3, 5, 7, 6, 8]):
        await tap(turn % 2, "move", cell=cell)
    final = await group_session.load(CHAT_ID)
    assert final["status"] == "ended" and final["winner"] is None
    assert final["deltas"] == [0, 0]
    for actor in actors[:2]:
        stat = await GroupGameRepo(session).get_or_create(CHAT_ID, "xo", actor.id)
        assert stat.draws == 1 and stat.score == 1000
    await tap(2, "rematch")
    assert (await group_session.load(CHAT_ID))["match_id"] == final["match_id"]
    await tap(1, "rematch")
    lobby = await group_session.load(CHAT_ID)
    assert lobby["status"] == "lobby" and lobby["host"] == actors[1].id
    assert lobby["match_id"] != final["match_id"]


async def test_disabled_games_reject_existing_buttons(group, monkeypatch) -> None:
    tap, _actors, _message = group
    await active_match(tap, "xo")
    monkeypatch.setattr(group_router, "_enabled", AsyncMock(return_value=False))
    await tap(0, "move", cell=0)
    assert (await group_session.load(CHAT_ID))["board"] == "_________"


async def test_shared_buttons_pass_media_panel_guard() -> None:
    user = User(id=666, is_bot=False, first_name="مریم")
    chat = Chat(id=CHAT_ID, type=ChatType.SUPERGROUP)
    message = Message(message_id=841, chat=chat, date=datetime.now(UTC))
    query = CallbackQuery(
        id="test",
        from_user=user,
        chat_instance="test",
        message=message,
        data=GroupGameCB(action="next", game="gol").pack(),
    )
    handler = AsyncMock(return_value="passed")
    assert await GroupPanelGuardMiddleware()(handler, query, {}) == "passed"


async def test_session_keeps_latest_state_after_a_redis_write_failure(monkeypatch) -> None:
    chat_id = CHAT_ID - 7
    redis = MagicMock(set=AsyncMock(), get=AsyncMock(), delete=AsyncMock())
    monkeypatch.setattr(group_session, "get_redis", lambda: redis)
    await group_session.save(chat_id, {"revision": 0, "players": []})
    redis.get.return_value = redis.set.await_args.args[1]
    redis.set.side_effect = ConnectionError("Redis unavailable")
    await group_session.save(chat_id, {"revision": 1, "players": []})
    state = await group_session.load(chat_id)
    assert state["revision"] == 1
    state["players"].append({"id": 1})
    assert (await group_session.load(chat_id))["players"] == []
    await group_session.drop(chat_id)


async def test_dispatcher_only_handles_addressed_group_commands(
    app_dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot_user = User(id=111111, is_bot=True, first_name="Mewvia", username="MewviaTestBot")
    chat = Chat(id=CHAT_ID - 5, type=ChatType.SUPERGROUP)

    class GameBot(FakeBot):
        async def __call__(self, method, request_timeout=None):
            await super().__call__(method, request_timeout)
            if isinstance(method, GetMe):
                return bot_user
            if isinstance(method, SendMessage):
                return Message(message_id=201, chat=chat, date=datetime.now(UTC))
            return None

    bot = GameBot()
    host = User(id=8121, is_bot=False, first_name="میزبان")
    guest = User(id=8122, is_bot=False, first_name="مهمان")
    monkeypatch.setattr(group_router, "_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(group_router, "_registry", AsyncMock(return_value=EmojiRegistry()))
    await group_session.drop(chat.id)
    try:
        for update_id, text in enumerate(("/game_rps@other_bot", "/game_rps@MewviaTestBot"), 1):
            msg = Message(
                message_id=update_id, chat=chat, date=datetime.now(UTC), from_user=host, text=text
            )
            await app_dispatcher.feed_update(bot, Update(update_id=update_id, message=msg))
        sent = [call for call in bot.calls if isinstance(call, SendMessage)]
        assert len(sent) == 1 and "قیچی" in sent[0].text
        join = next(
            button.callback_data
            for row in sent[0].reply_markup.inline_keyboard
            for button in row
            if button.callback_data and GroupGameCB.unpack(button.callback_data).action == "join"
        )
        query = CallbackQuery(
            id="join",
            from_user=guest,
            chat_instance="test",
            data=join,
            message=Message(message_id=201, chat=chat, date=datetime.now(UTC)),
        )
        await app_dispatcher.feed_update(bot, Update(update_id=3, callback_query=query))
        assert len((await group_session.load(chat.id))["players"]) == 2
    finally:
        await group_session.drop(chat.id)
        await bot.session.close()


def test_rating_rules_and_callback_size() -> None:
    assert rating_delta("loss", 105, 0) == -5
    assert rating_delta("loss", 100, 0) == 0
    assert rating_delta("win", 1000, 8) == 35
    assert rating_delta("draw", 1000, 2) == 0
    cb = GroupGameCB(action="gol_guess", game="gol", side="empty", match_id="f" * 32, revision=16)
    assert len(cb.pack().encode()) <= 64 and GroupGameCB.unpack(cb.pack()) == cb


def test_rps_wins_and_ties() -> None:
    for choice in RPS_LABELS:
        assert rps_winner(choice, choice) is None
    for winner, loser in [("rock", "scissors"), ("paper", "rock"), ("scissors", "paper")]:
        assert rps_winner(winner, loser) == 0
        assert rps_winner(loser, winner) == 1


def test_group_command_menu_includes_games() -> None:
    from app.__main__ import GROUP_BOT_COMMANDS

    names = {cmd.command for cmd in GROUP_BOT_COMMANDS}
    assert {"game", "game_xo", "game_gol", "game_rps", "game_lb"} <= names


def test_games_category_contains_only_game_slots() -> None:
    from app.db.seed import EMOJI_SLOTS
    from app.features.uidemo import CATEGORIES, _category_slots

    games = next(category for category in CATEGORIES if category[0] == "games")
    ui = next(category for category in CATEGORIES if category[0] == "ui")
    assert games[2:] == (400, 479)
    assert ui[2] == 480
    names = {slot.slot for slot in _category_slots(EMOJI_SLOTS, "games")}
    assert {"xo_x", "xo_o", "trophy", "handshake", "bot_robot"} <= names
    assert not {"calendar", "reddit", "settings"} & names
