"""Typed callback data.

Every inline button uses a `CallbackData` factory rather than a hand-built
string, so a typo becomes a parse error at development time instead of a button
that silently does nothing.
"""

from __future__ import annotations

from app.callbacks.audio import AudioToolCB
from app.callbacks.batch import BatchCB
from app.callbacks.convert import ConvertCB
from app.callbacks.downloader import DownloaderCB
from app.callbacks.filter import FilterCB
from app.callbacks.image import ImageToolCB
from app.callbacks.nav import DemoCB, MenuCB, NoopCB, PageCB
from app.callbacks.text_overlay import TextOverlayCB
from app.callbacks.video import VideoToolCB
from app.callbacks.wide import WideCB

__all__ = [
    "AudioToolCB",
    "BatchCB",
    "ConvertCB",
    "DemoCB",
    "DownloaderCB",
    "FilterCB",
    "ImageToolCB",
    "MenuCB",
    "NoopCB",
    "PageCB",
    "TextOverlayCB",
    "VideoToolCB",
    "WideCB",
]
