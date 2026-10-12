"""Batch transformations callback data."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class BatchCB(CallbackData, prefix="batch"):
    """Callback data for multi-step batch transformations.

    Actions: 'menu', 'toggle_wide', 'set_filter', 'set_target', 'apply', 'clear'
    """

    action: str
    val: str = ""
