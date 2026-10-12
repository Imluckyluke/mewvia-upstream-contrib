"""Video tools callback data."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class VideoToolCB(CallbackData, prefix="vtool"):
    """Callback data for video tools actions.

    Actions: 'note', 'compress', 'mute', 'reverse', 'comp_level'
    """

    action: str
    level: str = ""
