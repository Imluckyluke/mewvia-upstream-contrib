"""Game callback data."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class GameCB(CallbackData, prefix="gm"):
    """Callback data for game navigation and gameplay."""

    action: str
    """Action: menu, diff, xo, move, reset, lb, profile, rules"""

    diff: str = "med"
    """Difficulty: easy, med, hard"""

    cell: int = 0
    """Board cell index (0..8)"""

    b: str = "_________"
    """Board state string: 9 chars composed of _, X, O"""
