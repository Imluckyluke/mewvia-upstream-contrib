"""Explicit group game commands and shared multiplayer panels."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import GroupGameCB
from app.core.settings_store import get_settings_store
from app.db.repo.user import UserRepo
from app.features.games import group_session
from app.features.games.group_leaderboard import render_leaderboard
from app.features.games.group_panels import render_match, render_menu
from app.features.games.group_rules import (
    GAME_LABELS,
    GOL_LABELS,
    GOL_ROUNDS,
    RPS_LABELS,
    rps_winner,
)
from app.features.games.group_scoring import settle_match
from app.features.games.xo_engine import check_winner
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import inline_button
from app.ui.styles import Style

router = Router(name="games_group")
GAMES_GROUP_SETTING = "games_group_enabled"
_GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
_DIRECT = {
    "game_xo": "xo",
    "دوز": "xo",
    "game_gol": "gol",
    "گل_یا_پوچ": "gol",
    "game_rps": "rps",
    "سنگ_کاغذ_قیچی": "rps",
}
_MENUS = {"game", "games", "بازی", "بازی_گروهی"}
_LEADERBOARDS = {"game_lb", "lb", "لیدربرد"}
_EXPIRED = "این دکمه قدیمی شده. آخرین پیام بازی رو باز کن یا /game رو بفرست."


async def _enabled() -> bool:
    return await get_settings_store().get_bool(GAMES_GROUP_SETTING, False)


class GroupGamesEnabled(BaseFilter):
    async def __call__(self, message: Message) -> dict[str, str] | bool:
        if message.chat.type not in _GROUP_TYPES:
            return False
        words = (message.text or "").split(maxsplit=1)
        if not words or not words[0].startswith("/"):
            return False
        command, _, mention = words[0][1:].lower().partition("@")
        if command not in _DIRECT and command not in _MENUS and command not in _LEADERBOARDS:
            return False
        if mention:
            bot = message.bot
            if bot is None or mention != ((await bot.me()).username or "").lower():
                return False
        return {"command": command} if await _enabled() else False


async def _registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


def _active(state: dict[str, Any] | None) -> bool:
    return bool(
        state and state.get("match_id") and state["status"] in {"lobby", "playing", "settling"}
    )


def _menu(
    registry: EmojiRegistry, state: dict[str, Any] | None
) -> tuple[str, InlineKeyboardMarkup]:
    text, markup = render_menu(registry)
    if state and _active(state):
        text += f"\n\nدر جریان: <b>{GAME_LABELS[state['game']]}</b>"
        actions = [
            inline_button(
                "بازنشر بازی برای بازیکن‌ها",
                callback_data=GroupGameCB(action="resume", game=state["game"]).pack(),
                style=Style.PRIMARY,
                registry=registry,
                emoji_slot="game",
            )
        ]
        if state.get("url"):
            actions.insert(0, inline_button("رفتن به بازی", url=state["url"], registry=registry))
        markup.inline_keyboard.insert(0, actions)
    return text, markup


async def _player(session: AsyncSession, actor: User) -> dict[str, int | str]:
    user = await UserRepo(session).upsert_from_telegram(actor)
    player: dict[str, int | str] = {"id": user.telegram_id, "name": user.full_name}
    # Don't expose a player in Redis before their database row exists for scoring.
    await session.commit()
    return player


async def _lobby(session: AsyncSession, actor: User, game: str) -> dict[str, Any]:
    return {
        "match_id": uuid4().hex,
        "revision": 0,
        "game": game,
        "status": "lobby",
        "host": actor.id,
        "players": [await _player(session, actor)],
    }


async def _edit(message: Message, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in exc.message.lower():
            raise


async def _publish(message: Message, state: dict[str, Any], registry: EmojiRegistry) -> None:
    text, markup = render_match(state, registry)
    sent = await message.answer(text, reply_markup=markup)
    state["message_id"] = sent.message_id
    state["url"] = sent.get_url(include_thread_id=True)
    await group_session.save(message.chat.id, state)


@router.message(GroupGamesEnabled())
async def cmd_group_game(message: Message, session: AsyncSession, command: str) -> None:
    actor = message.from_user
    if not actor or actor.is_bot or message.sender_chat:
        return
    chat_id = message.chat.id
    registry = await _registry()
    if command in _LEADERBOARDS:
        text, markup = await render_leaderboard(session, chat_id, "all", 0, registry)
        await message.answer(text, reply_markup=markup)
        return

    async with group_session.lock(chat_id):
        state = await group_session.load(chat_id)
        if command not in _DIRECT or _active(state):
            text, markup = _menu(registry, state)
            await message.answer(text, reply_markup=markup)
            return
        await _publish(message, await _lobby(session, actor, _DIRECT[command]), registry)


async def _finish(
    session: AsyncSession, chat_id: int, state: dict[str, Any], winner: int | None
) -> None:
    if state["status"] != "settling":
        # Save the decisive move before changing scores. Retries cannot pick a different result.
        state.update(status="settling", winner=winner)
        await group_session.save(chat_id, state)
    result = await settle_match(
        session,
        chat_id,
        state["game"],
        state["match_id"],
        [p["id"] for p in state["players"]],
        winner,
    )
    await session.commit()
    state.update(result, status="ended", winner=winner)


async def _play(
    session: AsyncSession, message: Message, actor: User, cb: GroupGameCB, state: dict[str, Any]
) -> tuple[str, bool]:
    """Apply one authorized action; return a private toast and whether to redraw."""
    action, game = cb.action, state["game"]
    status = state["status"]
    players = state["players"]
    ids = [p["id"] for p in players]
    chat_id = message.chat.id
    if action == "join" and status == "lobby":
        if actor.id in ids:
            return "شما قبلاً وارد شده‌اید.", False
        if len(players) != 1:
            return "بازی پر است.", False
        players.append(await _player(session, actor))
        return "وارد بازی شدید.", True
    if actor.id not in ids:
        return "این دکمه فقط برای بازیکن‌های همین بازی است.", False
    if status == "settling":
        await _finish(session, chat_id, state, state["winner"])
        return "", True
    index = ids.index(actor.id)
    if action == "cancel" and (status == "playing" or (status == "lobby" and index == 0)):
        state["status"] = "cancelled"
        return "بازی بدون ثبت امتیاز لغو شد.", True
    if status == "lobby":
        if action == "leave" and index == 1:
            players.pop()
            return "از بازی خارج شدید.", True
        if action == "start" and index == 0 and len(players) == 2:
            state["status"] = "playing"
            if game == "xo":
                state.update(board="_" * 9, turn=0)
            elif game == "gol":
                state.update(round=1, points=[0, 0], phase="hide")
            else:
                state["picks"] = {}
            return "بازی شروع شد!", True
        return "فقط میزبان می‌تواند پس از ورود نفر دوم بازی را شروع یا لغو کند.", False

    if status != "playing":
        return _EXPIRED, False
    if game == "xo" and action == "move":
        if index != state["turn"]:
            return "نوبت شما نیست.", False
        cell, board = cb.cell, state["board"]
        if not 0 <= cell < 9 or board[cell] != "_":
            return "این خانه قابل انتخاب نیست.", False
        state["board"] = board[:cell] + "XO"[index] + board[cell + 1 :]
        mark, line = check_winner(state["board"])
        if mark is None:
            state["turn"] = 1 - index
        else:
            state["winning_cells"] = list(line or ())
            await _finish(session, chat_id, state, index if line else None)
        return "", True

    if game == "gol":
        hider = (state["round"] - 1) % 2
        phase = state["phase"]
        if action == "gol_hide" and phase == "hide" and index == hider and cb.side in GOL_LABELS:
            state.update(hidden=cb.side, phase="guess")
            return "انتخابت مخفیانه ثبت شد.", True
        if action == "gol_guess" and phase == "guess" and index != hider and cb.side in GOL_LABELS:
            hit = cb.side == state["hidden"]
            points = state["points"]
            points[index if hit else hider] += 2 if hit else 1
            state.update(guess=cb.side, phase="reveal")
            if state["round"] == GOL_ROUNDS:
                winner = None if points[0] == points[1] else int(points[1] > points[0])
                await _finish(session, chat_id, state, winner)
            return "", True
        if action == "next" and phase == "reveal":
            state.update(round=state["round"] + 1, phase="hide")
            state.pop("hidden", None)
            state.pop("guess", None)
            return "", True
        return "نوبت شما یا زمان این حرکت نیست.", False

    if game == "rps" and action == "rps_pick" and cb.side in RPS_LABELS:
        picks = state["picks"]
        if str(actor.id) in picks:
            return "انتخاب شما قبلاً ثبت شده است.", False
        picks[str(actor.id)] = cb.side
        if len(picks) == 1:
            # Both players must be able to use the same keyboard revision.
            await group_session.save(chat_id, state)
            return "انتخابت ثبت شد؛ منتظر حریف باش.", False
        await _finish(session, chat_id, state, rps_winner(picks[str(ids[0])], picks[str(ids[1])]))
        return "", True
    return _EXPIRED, False


@router.callback_query(GroupGameCB.filter())
async def cb_group_game(
    query: CallbackQuery, callback_data: GroupGameCB, session: AsyncSession
) -> None:
    message, cb = query.message, callback_data
    if not isinstance(message, Message) or message.chat.type not in _GROUP_TYPES:
        await query.answer()
        return
    if not await _enabled():
        await query.answer("بازی‌های گروهی غیرفعال‌اند.", show_alert=True)
        return
    chat_id = message.chat.id
    registry = await _registry()
    if cb.action == "lb":
        await query.answer()
        text, markup = await render_leaderboard(session, chat_id, cb.game, cb.cell, registry)
        if cb.match_id:
            await message.answer(text, reply_markup=markup)
        else:
            await _edit(message, text, markup)
        return

    async with group_session.lock(chat_id):
        state = await group_session.load(chat_id)
        if not cb.match_id:
            if cb.action == "menu":
                await query.answer()
                await _edit(message, *_menu(registry, state))
            elif cb.action == "resume" and state and _active(state):
                if query.from_user.id not in {p["id"] for p in state["players"]}:
                    await query.answer(
                        "فقط بازیکن‌ها می‌توانند بازی را بازنشر کنند.", show_alert=True
                    )
                else:
                    await query.answer()
                    if state["status"] == "settling":
                        await _finish(session, chat_id, state, state["winner"])
                        state["revision"] += 1
                        await group_session.save(chat_id, state)
                    await _publish(message, state, registry)
            elif cb.action == "create" and cb.game in GAME_LABELS and not _active(state):
                await query.answer()
                await _publish(message, await _lobby(session, query.from_user, cb.game), registry)
            elif cb.action == "create" and _active(state):
                await query.answer("اول بازی در جریان را تمام کنید.", show_alert=True)
                await _edit(message, *_menu(registry, state))
            else:
                await query.answer(_EXPIRED, show_alert=True)
            return

        if not state or (cb.match_id, cb.game, cb.revision, message.message_id) != (
            state.get("match_id"),
            state["game"],
            state.get("revision"),
            state.get("message_id"),
        ):
            await query.answer(_EXPIRED, show_alert=True)
            return
        if cb.action == "rematch" and state["status"] == "ended":
            if query.from_user.id not in {p["id"] for p in state["players"]}:
                await query.answer("فقط بازیکن‌ها می‌توانند دوباره بازی کنند.", show_alert=True)
                return
            await query.answer()
            await _publish(message, await _lobby(session, query.from_user, cb.game), registry)
            return

        toast, redraw = await _play(session, message, query.from_user, cb, state)
        if redraw:
            state["revision"] += 1
            await group_session.save(chat_id, state)
        await query.answer(toast or None)
        if redraw and state["status"] == "cancelled":
            text, markup = _menu(registry, state)
            await _edit(message, f"<b>{toast}</b>\n\n{text}", markup)
        elif redraw:
            await _edit(message, *render_match(state, registry))


@router.callback_query(F.data.startswith("gg:"))
async def cb_expired_group_game(query: CallbackQuery) -> None:
    await query.answer(_EXPIRED, show_alert=True)
