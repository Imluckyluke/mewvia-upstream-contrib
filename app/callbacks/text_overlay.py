"""CallbackData schema for Persian Text Overlay feature."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class TextOverlayCB(CallbackData, prefix="txtov"):
    action: str  # "start", "set_font", "set_color", "set_pos", "set_size", "render", "cancel"
    font: str = "vazir"
    color: str = "white"
    pos: str = "bottom"
    size: str = "medium"
