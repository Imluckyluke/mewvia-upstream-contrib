"""Pagination.

The reference admin panel shows a single row with a page counter and the next
button (`📄 ۱ / ۷` | `بعد »`). This module produces the same layout for any list,
so the emoji panel, the user list and future screens share one implementation and
one set of edge cases.

Rules encoded here:

* page numbers are 0-based internally, 1-based on screen;
* the counter uses Persian digits;
* `« قبل` is omitted on the first page and `بعد »` on the last, so no button ever
  does nothing;
* an empty list still renders as page 1 of 1 rather than 1 of 0.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from aiogram.types import InlineKeyboardButton

from app.core.units import fa_digits
from app.ui.emoji import EmojiRegistry
from app.ui.kb import inline_button
from app.ui.styles import Style
from app.ui.texts import t

DEFAULT_PER_PAGE = 8

T = TypeVar("T")


def total_pages(total_items: int, per_page: int) -> int:
    """At least 1 — an empty list is page 1 of 1, not 1 of 0."""
    if per_page <= 0 or total_items <= 0:
        return 1
    return -(-total_items // per_page)  # ceil without float maths


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: list[T]
    index: int
    """0-based page number."""
    total_items: int
    per_page: int

    @property
    def total_pages(self) -> int:
        return total_pages(self.total_items, self.per_page)

    @property
    def human_index(self) -> int:
        return self.index + 1

    @property
    def has_prev(self) -> bool:
        return self.index > 0

    @property
    def has_next(self) -> bool:
        return self.index + 1 < self.total_pages

    @property
    def offset(self) -> int:
        return self.index * self.per_page

    @property
    def counter(self) -> str:
        """`۱ / ۷` — Persian digits, as in the reference panel."""
        return fa_digits(f"{self.human_index} / {self.total_pages}")


def clamp_page(index: int, total_items: int, per_page: int) -> int:
    """Keep a requested page inside the valid range.

    Callback data can name a page that no longer exists — a row was deleted, or an
    old message was tapped. Clamping means a stale button shows the nearest real
    page instead of an empty screen.
    """
    return max(0, min(index, total_pages(total_items, per_page) - 1))


def paginate(items: list[T], index: int, per_page: int = DEFAULT_PER_PAGE) -> Page[T]:
    """Slice an in-memory list. For DB-backed lists use `clamp_page` + LIMIT/OFFSET."""
    total = len(items)
    safe = clamp_page(index, total, per_page)
    start = safe * per_page
    return Page(
        items=items[start : start + per_page],
        index=safe,
        total_items=total,
        per_page=per_page,
    )


def nav_row(
    page: Page[object],
    *,
    callback_for_page: Callable[[int], str],
    registry: EmojiRegistry | None = None,
    noop_data: str = "noop",
) -> list[InlineKeyboardButton]:
    """The counter + prev/next row.

    `callback_for_page` takes a 0-based page index and returns callback data, so
    each screen keeps its own callback factory.
    """
    row: list[InlineKeyboardButton] = []

    if page.has_prev:
        row.append(
            inline_button(
                t("common.prev"),
                callback_data=callback_for_page(page.index - 1),
                style=Style.PRIMARY,
                emoji_slot="back",
                registry=registry,
            )
        )

    # The counter is a button because Telegram has no inline label; tapping it
    # does nothing on purpose.
    row.append(
        inline_button(
            page.counter,
            callback_data=noop_data,
            style=Style.NEUTRAL,
            emoji_slot="page",
            registry=registry,
        )
    )

    if page.has_next:
        row.append(
            inline_button(
                t("common.next"),
                callback_data=callback_for_page(page.index + 1),
                style=Style.PRIMARY,
                emoji_slot="next",
                registry=registry,
            )
        )

    return row
