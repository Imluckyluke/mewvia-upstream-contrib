"""Shazam audio identification callback data."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class ShazamCB(CallbackData, prefix="shz"):
    """Callback data for Shazam song identification and follow-up actions.

    Actions:
      - 'dl': download the identified song as MP3 320
      - 'lyrics': view expandable lyrics
      - 'close': close the identification card
    """

    action: str
    sid: str
