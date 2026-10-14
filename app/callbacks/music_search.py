"""Typed callback data for direct music search (Spotify & SoundCloud)."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class MusicSearchCB(CallbackData, prefix="msearch"):
    """Music search actions and track selection."""

    action: str  # select, switch, new, cancel
    platform: str = "spotify"  # spotify, soundcloud
    idx: int = 0  # 1-based index in results
    sid: str = ""  # 8-char hex session token
