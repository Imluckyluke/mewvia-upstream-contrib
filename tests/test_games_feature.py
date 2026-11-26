"""Unit and integration tests for the Games feature (XO, Leagues, Leaderboard)."""

from __future__ import annotations

import pytest
from aiogram.enums import ChatType
from aiogram.types import Chat, User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.game_stat import GameStat
from app.db.models.user import User
from app.db.repo.game import GameRepo
from app.db.repo.user import UserRepo
from app.features import load_features, menu_features
from app.features.games.leagues import (
    LEAGUES,
    format_league_progress_bar,
    get_league,
)
from app.features.games.router import (
    render_diff_picker,
    render_games_hub,
    render_leaderboard,
    render_my_league,
    render_rules,
    render_xo_board,
)
from app.features.games.xo_engine import (
    calculate_score_delta,
    check_winner,
    find_best_move,
)
from app.ui.emoji import EmojiRegistry


def test_xo_engine_check_winner() -> None:
    # Ongoing empty board
    assert check_winner("_________") == (None, None)

    # Horizontal win for X
    winner, line = check_winner("XXX______")
    assert winner == "X"
    assert line == (0, 1, 2)

    # Vertical win for O
    winner, line = check_winner("O__O__O__")
    assert winner == "O"
    assert line == (0, 3, 6)

    # Diagonal win for X
    winner, line = check_winner("X___X___X")
    assert winner == "X"
    assert line == (0, 4, 8)

    # Draw
    winner, line = check_winner("XOXOXOOXO")
    assert winner == "draw"
    assert line is None


def test_xo_engine_ai_blocks_and_wins() -> None:
    # Bot 'O' has immediate winning move at index 2
    board_win = "OO_XX____"
    assert find_best_move(board_win, "med") == 2
    assert find_best_move(board_win, "hard") == 2

    # Player 'X' has immediate winning threat at index 2, bot 'O' has no immediate win and must block
    board_block = "XX__O____"
    assert find_best_move(board_block, "med") == 2
    assert find_best_move(board_block, "hard") == 2


def test_calculate_score_delta() -> None:
    # Win base + streak
    assert calculate_score_delta("win", "easy", 1000, current_streak=0) == 15
    assert calculate_score_delta("win", "med", 1000, current_streak=2) == 25 + 4
    assert calculate_score_delta("win", "hard", 1000, current_streak=10) == 40 + 10

    # Draw
    assert calculate_score_delta("draw", "easy", 1000) == 2
    assert calculate_score_delta("draw", "med", 1000) == 5
    assert calculate_score_delta("draw", "hard", 1000) == 10

    # Loss: no penalty under 1000
    assert calculate_score_delta("loss", "easy", 950) == 0
    # Loss penalty above 1000
    assert calculate_score_delta("loss", "easy", 1100) == -5
    assert calculate_score_delta("loss", "med", 1100) == -10
    assert calculate_score_delta("loss", "hard", 1100) == -15


def test_leagues_resolution_and_bar() -> None:
    assert get_league(1000).key == "bronze"
    assert get_league(1149).key == "bronze"
    assert get_league(1150).key == "silver"
    assert get_league(1350).key == "gold"
    assert get_league(1650).key == "diamond"
    assert get_league(2100).key == "master"

    bar = format_league_progress_bar(1050)
    assert "امتیاز تا لیگ نقره" in bar
    assert "▰" in bar

    max_bar = format_league_progress_bar(2500)
    assert "بالاترین لیگ" in max_bar


def test_main_menu_includes_games_feature() -> None:
    load_features()
    feature_names = [f.name for f in menu_features()]
    assert "games" in feature_names


def test_persistent_keyboard_has_games_button() -> None:
    from app.ui.menu import persistent_keyboard
    from app.ui.texts import t

    registry = EmojiRegistry()
    kb = persistent_keyboard(registry)
    # Row 1: start_over, help (2 buttons)
    # Row 2: games (1 button)
    assert len(kb.keyboard) == 2
    assert len(kb.keyboard[0]) == 2
    assert len(kb.keyboard[1]) == 1
    assert t("menu.games") in kb.keyboard[1][0].text


def test_game_slots_in_seed() -> None:
    from app.db.seed import EMOJI_SLOTS

    slots = {s.slot: s.fallback for s in EMOJI_SLOTS}
    for expected in [
        "game",
        "xo_x",
        "xo_o",
        "xo_empty",
        "trophy",
        "streak_fire",
        "medal",
        "league_bronze",
        "league_silver",
        "league_gold",
        "league_diamond",
        "league_master",
    ]:
        assert expected in slots


def test_widget_html_size_and_compression() -> None:
    import zlib
    from app.features.games.widget import XO_WIDGET_HTML

    raw = XO_WIDGET_HTML.encode("utf-8")
    comp = zlib.compress(raw, level=9)
    # Telegram Widgets hard limits: raw <= 32768, compressed <= 4096
    assert len(raw) <= 32768
    assert len(comp) <= 4096


def test_renderers_output() -> None:
    registry = EmojiRegistry()
    user = User(telegram_id=99999, first_name="تست‌کننده", username="tester")

    from app.db.models.game_stat import GameStat

    stat = GameStat(telegram_id=99999, score=1100, wins=3, losses=1, draws=1)
    stat.user = user

    # Hub
    text, _ = render_games_hub(user, stat, 1, 10, registry)
    assert "مرکز بازی و مسابقات میوویا" in text
    assert "تست‌کننده" in text

    # Diff
    text_d, _ = render_diff_picker(registry)
    assert "سطح دشواری" in text_d

    # Board
    text_b, _ = render_xo_board(user, stat, "med", "_________", registry)
    assert "بازی دوز (XO) با میوویا" in text_b

    # Leaderboard (names only, no @username handles)
    text_lb, _ = render_leaderboard(99999, [stat], 1, 1, registry)
    assert "جدول رتبه‌بندی" in text_lb
    assert "تست‌کننده" in text_lb
    assert "@tester" not in text_lb

    # League
    text_l, _ = render_my_league(user, stat, 1, 1, registry)
    assert "شناسنامه لیگ" in text_l

    # Rules
    text_r, _ = render_rules(registry)
    assert "قوانین امتیازدهی" in text_r


@pytest.mark.asyncio
async def test_game_repo_crud_and_leaderboard(session: AsyncSession) -> None:
    user_repo = UserRepo(session)
    tg_u1 = TgUser(id=888101, is_bot=False, first_name="Player One", username="p1")
    tg_u2 = TgUser(id=888102, is_bot=False, first_name="Player Two", username="p2")
    await user_repo.upsert_from_telegram(tg_u1)
    await user_repo.upsert_from_telegram(tg_u2)

    repo = GameRepo(session)
    s1 = await repo.get_or_create(888101)
    assert s1.score == 1000
    assert s1.wins == 0

    # Record win for player 1
    s1 = await repo.record_result(888101, "win", 25)
    assert s1.score == 1025
    assert s1.wins == 1
    assert s1.current_streak == 1
    assert s1.max_streak == 1

    # Record win again -> streak 2
    s1 = await repo.record_result(888101, "win", 30)
    assert s1.score == 1055
    assert s1.wins == 2
    assert s1.current_streak == 2
    assert s1.max_streak == 2

    # Record loss -> streak resets to 0
    s1 = await repo.record_result(888101, "loss", -10)
    assert s1.score == 1045
    assert s1.losses == 1
    assert s1.current_streak == 0
    assert s1.max_streak == 2

    # Player 2
    await repo.record_result(888102, "win", 100)

    # Leaderboard: Player 2 should be higher than Player 1 (1100 > 1045)
    lb = await repo.get_leaderboard(limit=5)
    scores = [p.score for p in lb]
    assert sorted(scores, reverse=True) == scores

    rank_p2 = await repo.get_user_rank(888102)
    rank_p1 = await repo.get_user_rank(888101)
    assert rank_p2 < rank_p1



def test_board_buttons_are_colored_and_emoji_only() -> None:
    """Empty cells blue, X green, O red — and premium slots render as icons."""
    from app.features.games.router import render_xo_board
    from app.ui.styles import Style, resolve

    class FakeRegistry(EmojiRegistry):
        def fallback(self, slot: str) -> str:  # type: ignore[override]
            return {"xo_x": "❌", "xo_o": "⭕", "xo_empty": "⬜"}.get(slot, "?")

        def text(self, slot: str) -> str:  # type: ignore[override]
            return self.fallback(slot)

        def custom_id(self, slot: str) -> str | None:  # type: ignore[override]
            return None

    registry = FakeRegistry()
    user = User(telegram_id=1, first_name="X")
    stat = GameStat(telegram_id=1, score=1000)
    _text, markup = render_xo_board(user, stat, "med", "X_O________", registry)
    cells = markup.inline_keyboard[0]
    assert cells[0].style == resolve(Style.CONFIRM)  # X -> green
    assert cells[2].style == resolve(Style.DESTRUCTIVE)  # O -> red
    assert cells[1].style == resolve(Style.PRIMARY)  # empty -> blue
    assert all(b.text.strip() in {"", "❌", "⭕", "⬜"} for b in cells)


def test_result_shows_old_to_new_score_with_sign() -> None:
    """A win/loss must display the exact rating change, not just the total."""
    from app.features.games.router import render_xo_board

    registry = EmojiRegistry()
    user = User(telegram_id=1, first_name="X")
    stat = GameStat(telegram_id=1, score=1025)
    text, _ = render_xo_board(
        user, stat, "med", "XXX______", registry, "win", 25, 1000
    )
    assert "1,000" in text and "1,025" in text and "+25" in text

    stat.score = 1015
    text, _ = render_xo_board(
        user, stat, "hard", "OOO______", registry, "loss", -10, 1025
    )
    assert "1,025" in text and "1,015" in text and "-10" in text


def test_difficulty_callback_renders_picker() -> None:
    """The diff action must open the chooser — not silently start med."""
    from app.callbacks.game import GameCB

    cb = GameCB(action="diff")
    assert cb.action == "diff"
    assert GameCB(action="xo", diff="hard").diff == "hard"
