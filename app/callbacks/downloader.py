"""Typed callback data for social media downloader."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class DownloaderCB(CallbackData, prefix="dl"):
    """Media download actions."""

    action: str  # video, video_720, audio, gallery, cancel
    token: str  # 8-char hex token resolving to stored URL metadata
