"""Group multiplayer games.

Explicit invocation only (``/game``, ``/بازی``, ``/game_lb``): the bot stays
silent in groups otherwise. A group game starts as a lobby hosted by the first
player; the second player taps «ورود به بازی» and both names are shown in the
preview. The host then taps «شروع بازی». Ratings are recorded both globally and
per group+game, so later games get their own ladder automatically.
"""

from __future__ import annotations

import html
from typing import Any, Literal

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import GroupGameCB
from app.core.logging import get_logger
from app.core.settings_store import get_settings_store
from app.core.units import fa_digits
from app.db.models.user import User
from app.db.repo.game import GameRepo
from app.db.repo.group_game import GroupGameRepo
from app.db.repo.user import UserRepo
from app.features.games import group_session
from app.features.games.xo_engine import calculate_score_delta, check_winner
from app.middlewares.group_guard import store_panel
from app.ui.emoji import EmojiRegistry, em, get_emoji_registry
from app.ui.kb import KB
from app.ui.styles import Style

log = get_logger(__name__)

router = Router(name="games_group")

GAMES_GROUP_SETTING = "games_group_enabled"
SUPPORTED_GAMES = {"xo": "دوز (XO)", "gol": "گل یا پوچ"}
PLAYERS_PER_GAME = {"xo": 2, "gol": 2}
COMMANDS = frozenset({
    "game", "games", "بازی", "بازی_گروهی",
    "game_gol", "گل_یا_پوچ",
    "game_lb", "lb", "لیدربرد",
})
GOL_ROUNDS = 3
Outcome = Literal["win", "loss", "draw"]


async def _lock_panel(message: Message, owner_id: int, owner_name: str) -> None:
    """Bind a group game panel to the user who opened it."""
    await store_panel(
        message.chat.id,
        message.message_id,
        owner_id,
        owner_name,
        kind="group_game",
    )


def _game_label(game: str) -> str:
    return SUPPORTED_GAMES.get(game, game)


class GroupGamesEnabled(BaseFilter):
    """Only answer explicit game commands, and only while the switch is on."""

    async def __call__(self, message: Message) -> bool:
        if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            return False
        text = (message.text or "").strip()
        if not text.startswith("/"):
            return False
        cmd = text.split()[0][1:].split("@")[0].lower()
        if cmd not in COMMANDS:
            return False
        return await get_settings_store().get_bool(GAMES_GROUP_SETTING, False)


async def _registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


def _name(user: dict[str, Any]) -> str:
    return html.escape(str(user.get("name") or "بازیکن"))


async def _display_names(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = (await session.execute(select(User).where(User.telegram_id.in_(ids)))).scalars().all()
    return {u.telegram_id: u.full_name for u in rows}


async def _render_lobby(
    session: AsyncSession, state: dict[str, Any], registry: EmojiRegistry
) -> tuple[str, Any]:
    game = state.get("game", "xo")
    players: list[dict[str, Any]] = state.get("players", [])
    host = state.get("host") or (players[0]["id"] if players else 0)
    need = PLAYERS_PER_GAME.get(game, 2)
    if game == "gol":
        lines = [
            f"{registry.text('gol_ball')} "
            f"<b>بازی گروهی {html.escape(_game_label(str(game)))}</b>",
            f"{registry.text('league_master')} میزبان: "
            f"<b>{_name(players[0]) if players else '—'}</b>",
        ]
        for index in range(1, need):
            if index < len(players):
                lines.append(f"👤 بازیکن {index + 1}: <b>{_name(players[index])}</b>")
            else:
                lines.append(f"👤 بازیکن {index + 1}: <i>در انتظار ورود…</i>")
        lines.append(f"\n👥 {len(players)} / {need} بازیکن")
        lines.append(
            "\n<i>هر دست یکی گل رو مخفی می‌کنه و دیگری حدس می‌زنه؛ "
            "نقش‌ها هر دست عوض می‌شن.</i>"
        )
    else:
        lines = [
            f"{registry.text('game')} <b>بازی گروهی {html.escape(_game_label(str(game)))}</b>",
            f"👑 میزبان: <b>{_name(players[0]) if players else '—'}</b> {registry.text('xo_x')}",
        ]
        for index in range(1, need):
            if index < len(players):
                lines.append(f"👤 بازیکن {index + 1}: <b>{_name(players[index])}</b> ⭕")
            else:
                lines.append(f"👤 بازیکن {index + 1}: <i>در انتظار ورود…</i>")
        lines.append(f"\n👥 {len(players)} / {need} بازیکن")

    kb = KB(registry)
    if len(players) < need:
        kb.button(
            "ورود به بازی",
            callback_data=GroupGameCB(action="join", game=game).pack(),
            style=Style.CONFIRM,
            emoji_slot="add",
        )
    else:
        lines.append("\n✅ ظرفیت تکمیل شد.")
    if host:
        kb.button(
            "شروع بازی",
            callback_data=GroupGameCB(action="start", game=game).pack(),
            style=Style.PRIMARY if len(players) == need else Style.NEUTRAL,
            emoji_slot="ok",
        )
        kb.button(
            "لغو بازی",
            callback_data=GroupGameCB(action="cancel", game=game).pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="trash",
        )
    kb.button(
        "جدول گروه",
        callback_data=GroupGameCB(action="lb", game=game).pack(),
        style=Style.NEUTRAL,
        emoji_slot="trophy",
    )
    kb.adjust(1, 2, 1)
    return "\n".join(lines), kb.as_markup()


async def _render_board(
    state: dict[str, Any], registry: EmojiRegistry
) -> tuple[str, Any]:
    players: list[dict[str, Any]] = state["players"]
    board: str = state["board"]
    turn: int = int(state.get("turn", 0))
    game = state.get("game", "xo")
    finished = bool(state.get("finished"))

    x_name = _name(players[0])
    o_name = _name(players[1]) if len(players) > 1 else "؟"
    lines = [
        f"{registry.text('game')} <b>دوز گروهی — {html.escape(_game_label(str(game)))}</b>",
        f"<b>{registry.text('xo_x')} {x_name}</b> در برابر "
        f"<b>{registry.text('xo_o')} {o_name}</b>",
    ]
    if finished:
        outcome = state.get("outcome")
        if outcome == "X":
            lines.append(f"\n🎉 <b>{x_name} برنده شد!</b>")
        elif outcome == "O":
            lines.append(f"\n🎉 <b>{o_name} برنده شد!</b>")
        else:
            lines.append(f"\n{em('handshake')} <b>مساوی!</b>")
        delta0 = state.get("delta0", 0)
        delta1 = state.get("delta1", 0)
        lines.append(f"امتیاز گروهی: {x_name} <b>{delta0:+,}</b> | {o_name} <b>{delta1:+,}</b>")
    else:
        lines.append(f"\n⏳ نوبت: <b>{_name(players[turn])}</b>")

    kb = KB(registry)
    for index, char in enumerate(board):
        if char == "X":
            slot, style = "xo_x", Style.CONFIRM
        elif char == "O":
            slot, style = "xo_o", Style.DESTRUCTIVE
        else:
            slot, style = "xo_empty", Style.PRIMARY
        kb.button(
            "",
            callback_data=(
                GroupGameCB(action="move", game=game, cell=index).pack()
                if char == "_" and not finished
                else "noop"
            ),
            style=style,
            emoji_slot=slot,
        )
    kb.adjust(3, 3, 3)
    if finished:
        kb.button(
            "جدول گروه",
            callback_data=GroupGameCB(action="lb", game=game).pack(),
            style=Style.PRIMARY,
            emoji_slot="trophy",
        )
    return "\n".join(lines), kb.as_markup()


@router.message(GroupGamesEnabled())
async def cmd_group_game(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    if not message.from_user or not message.chat:
        return
    await state.clear()
    registry = await _registry()
    command = (message.text or "").split()[0][1:].split("@")[0].lower()

    if command in {"game_lb", "lb", "لیدربرد"}:
        await _show_leaderboard(
            message.chat.id,
            "xo",
            message.reply,
            session,
            registry,
            owner_id=message.from_user.id,
            owner_name=message.from_user.full_name,
        )
        return

    chat_id = message.chat.id
    existing = await group_session.load(chat_id)
    owner_id = message.from_user.id
    owner_name = message.from_user.full_name
    if existing and existing.get("status") == "playing":
        text, kb = await _render_board(existing, registry)
        sent = await message.reply(text, reply_markup=kb)
        if isinstance(sent, Message):
            await _lock_panel(sent, owner_id, owner_name)
        return
    if existing and existing.get("status") == "lobby":
        text, kb = await _render_lobby(session, existing, registry)
        sent = await message.reply(text, reply_markup=kb)
        if isinstance(sent, Message):
            await _lock_panel(sent, owner_id, owner_name)
        return

    game_type = "gol" if command in {"game_gol", "گل_یا_پوچ"} else "xo"
    user = await UserRepo(session).upsert_from_telegram(message.from_user)
    lobby: dict[str, Any] = {
        "game": game_type,
        "status": "lobby",
        "host": user.telegram_id,
        "players": [{"id": user.telegram_id, "name": user.full_name}],
    }
    await group_session.save(chat_id, lobby)
    text, kb = await _render_lobby(session, lobby, registry)
    sent = await message.reply(text, reply_markup=kb)
    if isinstance(sent, Message):
        await _lock_panel(sent, owner_id, owner_name)


async def _show_leaderboard(
    chat_id: int,
    game: str,
    responder: Any,
    session: AsyncSession,
    registry: EmojiRegistry,
    owner_id: int = 0,
    owner_name: str = "",
) -> None:
    repo = GroupGameRepo(session)
    rows = await repo.leaderboard(chat_id, game, limit=10)
    lines = [
        f"{registry.text('trophy')} <b>جدول گروهی {html.escape(_game_label(str(game)))}</b>",
        "",
    ]
    if not rows:
        lines.append("<i>هنوز بازیای در این گروه ثبت نشده است.</i>")
    else:
        names = await _display_names(session, [r.telegram_id for r in rows])
        medals = ["🥇", "🥈", "🥉"]
        for index, row in enumerate(rows):
            badge = medals[index] if index < 3 else f"<b>{index + 1}.</b>"
            name = html.escape(names.get(row.telegram_id, "بازیکن"))
            lines.append(
                f"{badge} <b>{name}</b> — <b>{row.score:,}</b> امتیاز "
                f"(برد: {row.wins} | باخت: {row.losses})"
            )
    kb = KB(registry)
    kb.button(
        "بازی گروهی",
        callback_data=GroupGameCB(action="lobby", game=game).pack(),
        style=Style.CONFIRM,
        emoji_slot="game",
    )
    sent = await responder("\n".join(lines), reply_markup=kb.as_markup())
    if owner_id and isinstance(sent, Message):
        await _lock_panel(sent, owner_id, owner_name)


@router.callback_query(GroupGameCB.filter(F.action == "lobby"))
async def cb_lobby(query: CallbackQuery, session: AsyncSession) -> None:
    if not isinstance(query.message, Message) or not query.from_user:
        return
    await query.answer()
    state = await group_session.load(query.message.chat.id)
    registry = await _registry()
    if not state or state.get("status") == "playing":
        text, kb = await _render_lobby(
            session,
            {
                "game": "xo",
                "status": "lobby",
                "host": query.from_user.id,
                "players": [
                    {"id": query.from_user.id, "name": query.from_user.full_name}
                ],
            },
            registry,
        )
    else:
        text, kb = await _render_lobby(session, state, registry)
    await query.message.edit_text(text, reply_markup=kb)


@router.callback_query(GroupGameCB.filter(F.action == "join"))
async def cb_join(query: CallbackQuery, session: AsyncSession) -> None:
    if not isinstance(query.message, Message) or not query.from_user:
        return
    chat_id = query.message.chat.id
    state = await group_session.load(chat_id)
    if not state or state.get("status") != "lobby":
        await query.answer("لابی منقضی شده است؛ دوباره /game بفرستید.", show_alert=True)
        return
    game = state.get("game", "xo")
    need = PLAYERS_PER_GAME.get(game, 2)
    players: list[dict[str, Any]] = state["players"]
    if any(p["id"] == query.from_user.id for p in players):
        await query.answer("شما قبلاً وارد بازی شدید.")
        return
    if len(players) >= need:
        await query.answer("ظرفیت بازی پر است.")
        return
    user = await UserRepo(session).upsert_from_telegram(query.from_user)
    players.append({"id": user.telegram_id, "name": user.full_name})
    await group_session.save(chat_id, state)
    registry = await _registry()
    text, kb = await _render_lobby(session, state, registry)
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer(f"✅ {user.full_name} وارد بازی شد.")


@router.callback_query(GroupGameCB.filter(F.action == "start"))
async def cb_start(query: CallbackQuery, session: AsyncSession) -> None:
    if not isinstance(query.message, Message) or not query.from_user:
        return
    chat_id = query.message.chat.id
    state = await group_session.load(chat_id)
    if not state or state.get("status") != "lobby":
        await query.answer("لابی منقضی شد؛ دوباره /game بفرستید.", show_alert=True)
        return
    if state.get("host") != query.from_user.id:
        await query.answer("فقط میزبان می‌تواند بازی را شروع کند.", show_alert=True)
        return
    game = state.get("game", "xo")
    need = PLAYERS_PER_GAME.get(game, 2)
    if len(state.get("players", [])) < need:
        await query.answer("هنوز بازیکن کافی وارد نشده است.", show_alert=True)
        return
    registry = await _registry()
    if game == "gol":
        players: list[dict[str, Any]] = state["players"]
        state.update(
            {
                "status": "playing",
                "round": 1,
                "points": {str(p["id"]): 0 for p in players},
                "gol_hide": "",
                "gol_guess": "",
                "gol_result": "",
                "gol_point_to": "",
            }
        )
        _gol_reset_hand(state)
        await group_session.save(chat_id, state)
        text, kb = await _render_gol(state, registry)
        await query.message.edit_text(text, reply_markup=kb)
        hitter, _ = _gol_roles(state)
        await query.answer(f"{em('fist')} شروع شد — {_name(hitter)} مخفی می‌کنه.")
        return

    state.update(
        {"status": "playing", "board": "_" * 9, "turn": 0, "finished": False}
    )
    await group_session.save(chat_id, state)
    text, kb = await _render_board(state, registry)
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer("🎮 بازی شروع شد!")


@router.callback_query(GroupGameCB.filter(F.action == "move"))
async def cb_move(query: CallbackQuery, callback_data: GroupGameCB, session: AsyncSession) -> None:
    if not isinstance(query.message, Message) or not query.from_user:
        return
    chat_id = query.message.chat.id
    state = await group_session.load(chat_id)
    if not state or state.get("status") != "playing":
        await query.answer("بازی قبلاً تمام شده است.", show_alert=True)
        return
    turn = int(state.get("turn", 0))
    players: list[dict[str, Any]] = state["players"]
    if turn >= len(players) or players[turn]["id"] != query.from_user.id:
        await query.answer("نوبت شما نیست!", show_alert=True)
        return
    cell = callback_data.cell
    board = state["board"]
    if not 0 <= cell <= 8 or board[cell] != "_":
        await query.answer("این خانه پر است.")
        return

    next_board = board[:cell] + ("X" if turn == 0 else "O") + board[cell + 1 :]
    winner, _ = check_winner(next_board)
    game = state.get("game", "xo")
    registry = await _registry()

    if winner is None:
        state.update({"board": next_board, "turn": 1 - turn})
        await group_session.save(chat_id, state)
        text, kb = await _render_board(state, registry)
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer()
        return

    # Finished — record global and per-group ratings for both players.
    global_repo = GameRepo(session)
    group_repo = GroupGameRepo(session)
    first, second = players[0]["id"], players[1]["id"]
    if winner == "X":
        outcomes: dict[int, Outcome] = {first: "win", second: "loss"}
    elif winner == "O":
        outcomes = {first: "loss", second: "win"}
    else:
        outcomes = {first: "draw", second: "draw"}

    deltas: dict[int, int] = {}
    for pid, outcome in outcomes.items():
        gstat = await global_repo.get_or_create(pid)
        gdelta = calculate_score_delta(outcome, "med", gstat.score, gstat.current_streak)
        await global_repo.record_result(pid, outcome, gdelta)

        pstat = await group_repo.get_or_create(chat_id, game, pid)
        pdelta = calculate_score_delta(
            outcome, "med", pstat.score, pstat.current_streak
        )
        await group_repo.record_result(chat_id, game, pid, outcome, pdelta)
        deltas[pid] = pdelta

    state.update(
        {
            "board": next_board,
            "status": "ended",
            "finished": True,
            "outcome": winner,
            "delta0": deltas[first],
            "delta1": deltas[second],
        }
    )
    await group_session.save(chat_id, state)
    text, kb = await _render_board(state, registry)
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer(f"{em('finish')} بازی تمام شد و امتیازها ثبت شد.")


@router.callback_query(GroupGameCB.filter(F.action == "lb"))
async def cb_leaderboard(
    query: CallbackQuery, callback_data: GroupGameCB, session: AsyncSession
) -> None:
    if not isinstance(query.message, Message):
        return
    await query.answer()
    registry = await _registry()
    game = callback_data.game if callback_data.game in SUPPORTED_GAMES else "xo"
    await _show_leaderboard(
        query.message.chat.id,
        game,
        query.message.edit_text,
        session,
        registry,
        owner_id=query.from_user.id if query.from_user else 0,
        owner_name=query.from_user.full_name if query.from_user else "",
    )


@router.callback_query(GroupGameCB.filter(F.action == "cancel"))
async def cb_cancel(query: CallbackQuery) -> None:
    if not isinstance(query.message, Message) or not query.from_user:
        return
    state = await group_session.load(query.message.chat.id)
    if not state or state.get("host") != query.from_user.id:
        await query.answer("فقط میزبان می‌تواند لغو کند.", show_alert=True)
        return
    await group_session.drop(query.message.chat.id)
    registry = await _registry()
    await query.message.edit_text(
        f"{registry.text('trash')} بازی گروهی لغو شد.",
        reply_markup=KB(registry).as_markup(),
    )
    await query.answer("لغو شد.")


# ---------------------------------------------------------------------------
# گل یا پوچ — دو نفره: یکی مخفی می‌کند، دیگری حدس می‌زند.
#
# هر دست سه فاز دارد:
#   hide   → مخفی‌کننده یکی از سه گزینه را می‌زند: مشت چپ، مشت راست، یا
#            «دستم خالیه» (بلوف). هیچ‌کدام در پیام یا توست نشان داده نمی‌شود.
#   guess  → حدس‌زننده یکی از سه گزینه را می‌زند: چپ، راست، یا «پوچش کن».
#   reveal → گل رو می‌شود و امتیاز دست ثبت می‌شود.
#
# ماتریس امتیاز (سود حدس‌زننده) عمداً صفر-جمع است:
#
#            چپ    راست   پوچ
#   چپ       ۲     -۱     -۱
#   راست     -۱      ۲     -۱
#   خالی     -۱     -۱      ۲
#
# یعنی هیچ انتخابی برندهٔ تضمینی نیست و بلوف کردن مخفی‌کننده هم ریسک دارد؛
# هر دو طرف بهترین بازی را ۱/۳-۱/۳-۱/۳ انجام دهند. نقش‌ها هر دست عوض می‌شوند.
# ---------------------------------------------------------------------------

GOL_HANDS = ("left", "right")
GOL_EMPTY = "empty"
GOL_POOF = "poof"
GOL_HAND_LABEL = {"left": "دست چپ", "right": "دست راست", GOL_EMPTY: "دست خالی"}
GOL_HIT_POINTS = 2
GOL_MISS_POINTS = 1


def _gol_hitter_index(state: dict[str, Any]) -> int:
    """Roles alternate every hand: hand 1 → host hides, hand 2 → guest hides…"""
    return (int(state.get("round", 1)) - 1) % 2


def _gol_roles(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    players: list[dict[str, Any]] = state["players"]
    index = _gol_hitter_index(state)
    return players[index], players[1 - index]


def _gol_points(state: dict[str, Any]) -> dict[str, int]:
    points: dict[str, int] = state.setdefault("points", {})
    return points


def _gol_score(state: dict[str, Any], pid: object) -> int:
    return int(_gol_points(state).get(str(pid), 0))


def _gol_add(state: dict[str, Any], pid: object, amount: int) -> None:
    points = _gol_points(state)
    key = str(pid)
    points[key] = int(points.get(key, 0)) + amount


def _gol_phase(state: dict[str, Any]) -> str:
    return str(state.get("gol_phase") or "hide")


def _gol_reset_hand(state: dict[str, Any]) -> None:
    state.update(
        {
            "gol_phase": "hide",
            "gol_hide": "",
            "gol_guess": "",
            "gol_result": "",
            "gol_point_to": "",
        }
    )


def _gol_scoreboard(state: dict[str, Any], registry: EmojiRegistry) -> list[str]:
    hitter, guesser = _gol_roles(state)
    rows: list[str] = []
    for player, role_slot, role_text in (
        (hitter, "gol_lock", "مخفی‌کننده"),
        (guesser, "gol_guesser", "حدس‌زننده"),
    ):
        rows.append(
            f"{registry.text(role_slot)} <b>{_name(player)}</b> — {role_text} — "
            f"<b>{fa_digits(str(_gol_score(state, player['id'])))}</b> امتیاز"
        )
    return rows


async def _render_gol(state: dict[str, Any], registry: EmojiRegistry) -> tuple[str, Any]:
    phase = _gol_phase(state)
    hand_no = int(state.get("round", 1))
    hitter, guesser = _gol_roles(state)
    kb = KB(registry)

    lines = [
        f"{registry.text('gol_ball')} <b>گل یا پوچ — دو نفره</b>",
        f"دست <b>{fa_digits(str(hand_no))}</b> از {fa_digits(str(GOL_ROUNDS))}",
        "",
        *_gol_scoreboard(state, registry),
        "",
    ]

    if phase == "hide":
        lines.extend(
            [
                f"{registry.text('gol_lock')} <b>{_name(hitter)}</b>، گل رو مخفی کن.",
                "<i>می‌تونی یه مشت رو انتخاب کنی یا بلوف بزنی و دستت رو خالی بذاری. "
                "هیچ‌کس نمی‌فهمه کدوم رو زدی.</i>",
            ]
        )
        for hand in GOL_HANDS:
            kb.button(
                f"توی {GOL_HAND_LABEL[hand]}",
                callback_data=GroupGameCB(
                    action="gol_hide", game="gol", side=hand
                ).pack(),
                style=Style.PRIMARY,
                emoji_slot="fist",
            )
        kb.button(
            "دستم خالیه (بلوف)",
            callback_data=GroupGameCB(
                action="gol_hide", game="gol", side=GOL_EMPTY
            ).pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="gol_hidden",
        )
        kb.adjust(2, 1)
        return "\n".join(lines), kb.as_markup()

    if phase == "guess":
        lines.extend(
            [
                f"{registry.text('target')} <b>{_name(guesser)}</b>، گل کجاست؟",
                "",
                f"• گرفتن گل → <b>{fa_digits(str(GOL_HIT_POINTS))}</b> امتیاز",
                f"• پوچ کردن درست → <b>{fa_digits(str(GOL_HIT_POINTS))}</b> امتیاز",
                f"• هر اشتباهی → <b>{fa_digits(str(GOL_MISS_POINTS))}</b> امتیاز "
                f"برای {_name(hitter)}",
            ]
        )
        for hand in GOL_HANDS:
            kb.button(
                f"گل توی {GOL_HAND_LABEL[hand]}ه",
                callback_data=GroupGameCB(
                    action="gol_guess", game="gol", side=hand
                ).pack(),
                style=Style.CONFIRM,
                emoji_slot="fist",
            )
        kb.button(
            "پوچش کن! (هر دو خالی)",
            callback_data=GroupGameCB(action="gol_guess", game="gol", side=GOL_POOF).pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
        )
        kb.adjust(2, 1)
        return "\n".join(lines), kb.as_markup()

    # reveal
    hidden = str(state.get("gol_hide") or "")
    guess = str(state.get("gol_guess") or "")
    result = str(state.get("gol_result") or "")
    hitter_name, guesser_name = _name(hitter), _name(guesser)

    if hidden == GOL_EMPTY:
        lines.append(f"{registry.text('gol_hidden')} <b>هر دو مشت خالی بود!</b>")
    else:
        lines.append(
            f"{registry.text('gol_ball')} گل توی <b>{GOL_HAND_LABEL.get(hidden, '—')}</b> بود."
        )
    lines.append(
        f"{registry.text('target')} حدس {guesser_name}: "
        f"<b>{GOL_HAND_LABEL.get(guess, '—') if guess != GOL_POOF else 'پوچش کن'}</b>"
    )
    lines.append("")

    if result == "hit":
        lines.append(
            f"{registry.text('trophy')} <b>{guesser_name}</b> گل رو گرفت! "
            f"<b>+{fa_digits(str(GOL_HIT_POINTS))}</b>"
        )
    elif result == "poof_ok":
        lines.append(
            f"{registry.text('trophy')} <b>{guesser_name}</b> درست پوچ کرد! "
            f"<b>+{fa_digits(str(GOL_HIT_POINTS))}</b>"
        )
    elif result == "bluff":
        lines.append(
            f"{registry.text('mask')} بلوف <b>{hitter_name}</b> جواب داد — "
            f"<b>+{fa_digits(str(GOL_MISS_POINTS))}</b> برای اون"
        )
    else:  # miss
        lines.append(
            f"{registry.text('shield')} اشتباه! <b>{hitter_name}</b> گل رو رد کرد — "
            f"<b>+{fa_digits(str(GOL_MISS_POINTS))}</b> برای اون"
        )

    lines.append("")
    lines.append(
        f"{registry.text('medal')} مجموع: <b>{hitter_name}</b> "
        f"{fa_digits(str(_gol_score(state, hitter['id'])))} — "
        f"<b>{guesser_name}</b> {fa_digits(str(_gol_score(state, guesser['id']))) }"
    )

    last = hand_no >= GOL_ROUNDS
    kb.button(
        "نتیجهٔ نهایی" if last else "دست بعدی",
        callback_data=GroupGameCB(action="gol_next", game="gol").pack(),
        style=Style.CONFIRM,
        emoji_slot="next",
    )
    kb.adjust(1)
    return "\n".join(lines), kb.as_markup()


def _gol_resolve(hidden: str, guess: str) -> tuple[str, str]:
    """Return ``(result, point_to)`` — ``point_to`` is ``hitter`` or ``guesser``.

    Zero-sum payoff matrix for the guesser:

        hidden \\ guess   left   right   poof
        left               +2      -1     -1
        right              -1      +2     -1
        empty              -1      -1     +2
    """
    if hidden == GOL_EMPTY and guess == GOL_POOF:
        return "poof_ok", "guesser"
    if hidden != GOL_EMPTY and guess == hidden:
        return "hit", "guesser"
    if hidden == GOL_EMPTY:
        # The hider bluffed and the guesser named a hand → the bluff paid off.
        return "bluff", "hitter"
    return "miss", "hitter"


@router.callback_query(GroupGameCB.filter(F.action == "gol_hide"))
async def cb_gol_hide(
    query: CallbackQuery, callback_data: GroupGameCB, session: AsyncSession
) -> None:
    """Only the current hider may hide, only once, and the pick stays secret."""
    if not isinstance(query.message, Message) or not query.from_user:
        return
    chat_id = query.message.chat.id
    state = await group_session.load(chat_id)
    if not state or state.get("game") != "gol" or state.get("status") != "playing":
        await query.answer("این دست تمام شده است.", show_alert=True)
        return
    if _gol_phase(state) != "hide":
        await query.answer("گل مخفی شده — الان نوبت حدس زدنه.", show_alert=True)
        return
    hitter, _ = _gol_roles(state)
    if query.from_user.id != int(hitter["id"]):
        await query.answer(
            f"فقط {_name(hitter)} می‌تونه این دست رو مخفی کنه. "
            "دست بعدی نوبت شماست.",
            show_alert=True,
        )
        return
    side = callback_data.side
    if side not in (*GOL_HANDS, GOL_EMPTY):
        return
    # The pick is written to state only — never echoed in the message or the toast.
    state.update({"gol_hide": side, "gol_phase": "guess"})
    await group_session.save(chat_id, state)
    registry = await _registry()
    text, kb = await _render_gol(state, registry)
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer(f"{em('gol_lock')} ثبت شد — هیچ‌کس ندید کدوم رو زدی.")


@router.callback_query(GroupGameCB.filter(F.action == "gol_guess"))
async def cb_gol_guess(
    query: CallbackQuery, callback_data: GroupGameCB, session: AsyncSession
) -> None:
    """The guesser fires once; the payoff matrix in ``_gol_resolve`` decides."""
    if not isinstance(query.message, Message) or not query.from_user:
        return
    chat_id = query.message.chat.id
    state = await group_session.load(chat_id)
    if not state or state.get("game") != "gol" or state.get("status") != "playing":
        await query.answer("این دست تمام شده است.", show_alert=True)
        return
    if _gol_phase(state) != "guess":
        await query.answer("الان نوبت حدس زدن نیست.", show_alert=True)
        return
    _, guesser = _gol_roles(state)
    if query.from_user.id != int(guesser["id"]):
        await query.answer(
            f"این دست رو {_name(guesser)} حدس می‌زنه. دست بعدی نوبت شماست.",
            show_alert=True,
        )
        return
    guess = callback_data.side
    if guess not in (*GOL_HANDS, GOL_POOF):
        return
    hidden = str(state.get("gol_hide") or "")
    if not hidden:
        await query.answer("مخفی‌کننده هنوز انتخابش رو نزده.", show_alert=True)
        return

    hitter, guesser = _gol_roles(state)
    result, point_to = _gol_resolve(hidden, guess)
    if point_to == "guesser":
        _gol_add(state, guesser["id"], GOL_HIT_POINTS)
        toast = f"{em('trophy')} درست بود! +{fa_digits(str(GOL_HIT_POINTS))} امتیاز"
    else:
        _gol_add(state, hitter["id"], GOL_MISS_POINTS)
        toast = f"{em('shield')} اشتباه! یک امتیاز برای {_name(hitter)}"

    state.update(
        {
            "gol_guess": guess,
            "gol_result": result,
            "gol_point_to": point_to,
            "gol_phase": "reveal",
        }
    )
    await group_session.save(chat_id, state)
    registry = await _registry()
    text, kb = await _render_gol(state, registry)
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer(toast)


@router.callback_query(GroupGameCB.filter(F.action == "gol_next"))
async def cb_gol_next(query: CallbackQuery, session: AsyncSession) -> None:
    """Advance to the next hand, or settle the match after the last one."""
    if not isinstance(query.message, Message) or not query.from_user:
        return
    chat_id = query.message.chat.id
    state = await group_session.load(chat_id)
    if not state or state.get("game") != "gol":
        await query.answer("بازی‌ای در جریان نیست.", show_alert=True)
        return
    if _gol_phase(state) != "reveal":
        await query.answer("این دست هنوز تموم نشده.", show_alert=True)
        return
    round_no = int(state.get("round", 1))
    if round_no < GOL_ROUNDS:
        state["round"] = round_no + 1
        _gol_reset_hand(state)
        await group_session.save(chat_id, state)
        registry = await _registry()
        text, kb = await _render_gol(state, registry)
        await query.message.edit_text(text, reply_markup=kb)
        hitter, _ = _gol_roles(state)
        await query.answer(f"{em('fist')} دست بعدی — {_name(hitter)} مخفی می‌کنه.")
        return

    await _gol_finish(query, session, state)


async def _gol_finish(
    query: CallbackQuery, session: AsyncSession, state: dict[str, Any]
) -> None:
    """Record ratings for both ladders and show the final scoreboard."""
    if not isinstance(query.message, Message):
        return
    chat_id = query.message.chat.id
    players: list[dict[str, Any]] = state["players"]
    ids = [int(p["id"]) for p in players]
    scores = {pid: _gol_score(state, pid) for pid in ids}
    first, second = ids

    if scores[first] == scores[second]:
        outcomes: dict[int, Outcome] = {first: "draw", second: "draw"}
    elif scores[first] > scores[second]:
        outcomes = {first: "win", second: "loss"}
    else:
        outcomes = {first: "loss", second: "win"}

    global_repo = GameRepo(session)
    group_repo = GroupGameRepo(session)
    deltas: dict[int, int] = {}
    for pid, outcome in outcomes.items():
        gstat = await global_repo.get_or_create(pid)
        gdelta = calculate_score_delta(outcome, "med", gstat.score, gstat.current_streak)
        await global_repo.record_result(pid, outcome, gdelta)
        pstat = await group_repo.get_or_create(chat_id, "gol", pid)
        pdelta = calculate_score_delta(outcome, "med", pstat.score, pstat.current_streak)
        await group_repo.record_result(chat_id, "gol", pid, outcome, pdelta)
        deltas[pid] = pdelta

    registry = await _registry()
    if scores[first] > scores[second]:
        winner: int | None = first
    elif scores[second] > scores[first]:
        winner = second
    else:
        winner = None
    lines = [
        f"{registry.text('trophy')} <b>پایان گل یا پوچ</b>",
        "",
    ]
    for pid in ids:
        name = _name(next(p for p in players if int(p["id"]) == pid))
        lines.append(
            f"• <b>{name}</b> — {fa_digits(str(scores[pid]))} امتیاز دستی — "
            f"ریتینگ <b>{fa_digits(f'{deltas[pid]:+,}'.replace('-', '−'))}</b>"
        )
    lines.append("")
    if winner is None:
        lines.append(f"{registry.text('handshake')} <b>مساوی شد!</b>")
    else:
        name = _name(next(p for p in players if int(p["id"]) == winner))
        lines.append(f"{registry.text('league_gold')} <b>{name}</b> برندهٔ بازی شد!")

    kb = KB(registry)
    kb.button(
        "جدول گروه",
        callback_data=GroupGameCB(action="lb", game="gol").pack(),
        style=Style.PRIMARY,
        emoji_slot="trophy",
    )
    kb.button(
        "دست بازی جدید",
        callback_data=GroupGameCB(action="gol_rematch", game="gol").pack(),
        style=Style.CONFIRM,
        emoji_slot="repeat",
    )
    kb.adjust(2)
    if isinstance(query.message, Message):
        await query.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
    state["status"] = "ended"
    await group_session.save(chat_id, state)
    await query.answer(f"{em('finish')} بازی تمام شد و امتیازها ثبت شد.")


@router.callback_query(GroupGameCB.filter(F.action == "gol_rematch"))
async def cb_gol_rematch(query: CallbackQuery, session: AsyncSession) -> None:
    """Reset the same lobby for another three-hand match."""
    if not isinstance(query.message, Message) or not query.from_user:
        return
    chat_id = query.message.chat.id
    state = await group_session.load(chat_id)
    if not state or state.get("game") != "gol":
        await query.answer("بازی‌ای برای تکرار نیست؛ /game_gol بفرستید.", show_alert=True)
        return
    players: list[dict[str, Any]] = state["players"]
    if query.from_user.id not in {int(p["id"]) for p in players}:
        await query.answer("شما بازیکن این بازی نیستید.", show_alert=True)
        return
    state.update(
        {
            "status": "playing",
            "round": 1,
            "points": {str(p["id"]): 0 for p in players},
            "gol_hide": "",
            "gol_guess": "",
            "gol_result": "",
            "gol_point_to": "",
        }
    )
    _gol_reset_hand(state)
    await group_session.save(chat_id, state)
    registry = await _registry()
    text, kb = await _render_gol(state, registry)
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer(f"{em('repeat')} دست جدید شروع شد!")
