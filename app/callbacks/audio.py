"""Audio tools callback data."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class AudioToolCB(CallbackData, prefix="atool"):
    """Callback data for audio tools actions.

    Actions: 'to_voice', 'to_mp3', 'extract', 'speed_menu', 'set_speed',
    'tag_menu', 'tag_field', 'tag_cover', 'tag_done', 'tag_cancel'
    """

    action: str
    speed: str = ""
    bitrate: str = ""
    field: str = ""
