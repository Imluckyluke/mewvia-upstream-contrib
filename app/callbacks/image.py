"""Image tools callback data."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class ImageToolCB(CallbackData, prefix="itool"):
    """Callback data for image tools actions.

    Actions: 'resize', 'pdf_start', 'pdf_add', 'pdf_finish', 'extract_frames'
    """

    action: str
    dimension: int = 1024
