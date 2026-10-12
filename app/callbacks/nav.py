"""Navigation callbacks shared by every screen."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class NoopCB(CallbackData, prefix="noop"):
    """A button that intentionally does nothing (page counters, separators)."""


class MenuCB(CallbackData, prefix="menu"):
    """Main-menu navigation. `target` is a feature name, or "root"."""

    target: str


class PageCB(CallbackData, prefix="pg"):
    """Generic pagination. `scope` names the list being paged."""

    scope: str
    index: int


class DemoCB(CallbackData, prefix="demo"):
    """Phase-2 UI showcase & live emoji editor.

    `slot` is a separate field rather than being packed into `action`: aiogram
    joins fields with ":", so a value containing that character is rejected.
    """

    action: str
    slot: str = ""
    target: str = ""
