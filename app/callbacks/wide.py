"""Wide media feature callback data."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class WideCB(CallbackData, prefix="wide"):
    action: str  # "start"
