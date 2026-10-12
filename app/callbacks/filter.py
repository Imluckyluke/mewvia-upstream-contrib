"""Visual filters callback data."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class FilterCB(CallbackData, prefix="flt"):
    action: str  # "start", "apply"
    filter_id: str = "bw"
