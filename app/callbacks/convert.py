"""Conversion flow callback data."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class ConvertCB(CallbackData, prefix="conv"):
    """Callback data for conversion target selection.

    `kind` is the MediaKind string representation.
    `target` is the TargetSpec id (e.g. "webp", "webm_sticker", "gif", "png", "mp4").
    """

    kind: str
    target: str
