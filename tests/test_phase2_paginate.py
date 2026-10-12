"""Phase-2 acceptance tests, part 2: pagination, premium degradation, and the
menu built from the feature registry.
"""

from __future__ import annotations

import pytest
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from app.ui.paginate import DEFAULT_PER_PAGE, Page, clamp_page, nav_row, paginate, total_pages
from app.ui.premium_guard import demote_method, strip_tg_emoji

from tests.test_phase2_ui import PREMIUM_ID, loaded_registry

# ---------------------------------------------------------------------------
# Pagination arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "per_page", "expected"),
    [
        (0, 8, 1),  # empty list is page 1 of 1, never 1 of 0
        (1, 8, 1),
        (8, 8, 1),  # exactly full
        (9, 8, 2),
        (28, 8, 4),  # the seeded slot count
        (56, 8, 7),  # the reference panel's "۱ / ۷"
    ],
)
def test_total_pages(total: int, per_page: int, expected: int) -> None:
    assert total_pages(total, per_page) == expected


def test_total_pages_survives_a_zero_page_size() -> None:
    assert total_pages(10, 0) == 1


@pytest.mark.parametrize(
    ("index", "total", "expected"),
    [
        (-5, 28, 0),  # negative from hand-crafted callback data
        (0, 28, 0),
        (3, 28, 3),
        (99, 28, 3),  # past the end -> last page
        (2, 0, 0),  # list emptied since the message was sent
    ],
)
def test_clamp_page_keeps_stale_callbacks_usable(index: int, total: int, expected: int) -> None:
    assert clamp_page(index, total, DEFAULT_PER_PAGE) == expected


def test_paginate_slices_and_reports_position() -> None:
    items = list(range(28))
    page = paginate(items, 1, DEFAULT_PER_PAGE)
    assert page.items == list(range(8, 16))
    assert page.index == 1
    assert page.human_index == 2
    assert page.total_pages == 4
    assert page.offset == 8
    assert page.has_prev and page.has_next


def test_paginate_clamps_an_out_of_range_request() -> None:
    page = paginate(list(range(10)), 99, DEFAULT_PER_PAGE)
    assert page.index == 1
    assert page.items == [8, 9]
    assert not page.has_next


def test_paginate_on_an_empty_list() -> None:
    page = paginate([], 0, DEFAULT_PER_PAGE)
    assert page.items == []
    assert page.total_pages == 1
    assert page.counter == "۱ / ۱"
    assert not page.has_prev and not page.has_next


def test_counter_uses_persian_digits() -> None:
    page = Page[int](items=[], index=0, total_items=56, per_page=DEFAULT_PER_PAGE)
    assert page.counter == "۱ / ۷", "matches the reference panel"


# ---------------------------------------------------------------------------
# Navigation row
# ---------------------------------------------------------------------------


def _nav(index: int, total: int) -> list[InlineKeyboardButton]:
    page = Page[object](items=[], index=index, total_items=total, per_page=DEFAULT_PER_PAGE)
    return nav_row(page, callback_for_page=lambda i: f"pg:{i}", registry=loaded_registry())


def test_first_page_has_no_prev_button() -> None:
    """A button that cannot go anywhere must not exist."""
    row = _nav(0, 28)
    assert len(row) == 2
    assert row[1].callback_data == "pg:1"


def test_last_page_has_no_next_button() -> None:
    row = _nav(3, 28)
    assert len(row) == 2
    assert row[0].callback_data == "pg:2"


def test_middle_page_has_both() -> None:
    row = _nav(1, 28)
    assert [b.callback_data for b in row] == ["pg:0", "noop", "pg:2"]


def test_single_page_is_just_the_counter() -> None:
    row = _nav(0, 3)
    assert len(row) == 1
    assert row[0].callback_data == "noop"


def test_counter_button_is_inert() -> None:
    row = _nav(1, 28)
    counter = next(b for b in row if b.callback_data == "noop")
    assert "۲ / ۴" in counter.text


# ---------------------------------------------------------------------------
# Premium degradation (the retry payload)
# ---------------------------------------------------------------------------


def test_strip_tg_emoji_leaves_the_unicode_character() -> None:
    html = f'سلام <tg-emoji emoji-id="{PREMIUM_ID}">✅</tg-emoji> خوبی؟'
    assert strip_tg_emoji(html) == "سلام ✅ خوبی؟"


def test_strip_tg_emoji_handles_several_tags() -> None:
    html = '<tg-emoji emoji-id="1">✅</tg-emoji><tg-emoji emoji-id="2">❌</tg-emoji>'
    assert strip_tg_emoji(html) == "✅❌"


def test_strip_tg_emoji_is_a_noop_without_tags() -> None:
    assert strip_tg_emoji("plain ✅ text") == "plain ✅ text"


def test_demote_removes_tags_from_text_and_icons_from_buttons() -> None:
    from aiogram.methods import SendMessage

    method = SendMessage(
        chat_id=1,
        text=f'<tg-emoji emoji-id="{PREMIUM_ID}">✅</tg-emoji> سلام',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="A",
                        callback_data="a",
                        style="success",
                        icon_custom_emoji_id=PREMIUM_ID,
                    )
                ]
            ]
        ),
    )

    plain = demote_method(method)
    assert plain is not None
    assert plain.text == "✅ سلام"
    button = plain.reply_markup.inline_keyboard[0][0]
    assert button.icon_custom_emoji_id is None
    assert button.style == "success", "styles are always allowed and must survive"


def test_demote_inlines_the_fallback_on_the_button(monkeypatch: pytest.MonkeyPatch) -> None:
    """An icon that just vanishes reads as a bug; it must become a label prefix."""
    import app.ui.emoji as emoji_module
    from aiogram.methods import SendMessage

    monkeypatch.setattr(emoji_module, "_registry", loaded_registry())

    method = SendMessage(
        chat_id=1,
        text="سلام",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="دکمه", callback_data="a", icon_custom_emoji_id=PREMIUM_ID
                    )
                ]
            ]
        ),
    )
    plain = demote_method(method)
    assert plain is not None
    button = plain.reply_markup.inline_keyboard[0][0]
    assert button.icon_custom_emoji_id is None
    assert button.text == "✅ دکمه"


def test_demote_leaves_the_label_alone_for_an_unknown_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.ui.emoji as emoji_module
    from aiogram.methods import SendMessage

    monkeypatch.setattr(emoji_module, "_registry", loaded_registry())

    method = SendMessage(
        chat_id=1,
        text="x",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="دکمه", callback_data="a", icon_custom_emoji_id="999")]
            ]
        ),
    )
    plain = demote_method(method)
    assert plain is not None
    button = plain.reply_markup.inline_keyboard[0][0]
    assert button.icon_custom_emoji_id is None
    assert button.text == "دکمه"


def test_rejection_detector_also_catches_the_wording_telegram_really_uses() -> None:
    """Verified against api.telegram.org: an unusable id yields DOCUMENT_INVALID."""
    from app.ui.emoji import looks_like_custom_emoji_rejection

    assert looks_like_custom_emoji_rejection("Bad Request: DOCUMENT_INVALID")


def test_demote_handles_reply_keyboards_too() -> None:
    from aiogram.methods import SendMessage

    method = SendMessage(
        chat_id=1,
        text="plain",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="A", icon_custom_emoji_id=PREMIUM_ID)]],
            resize_keyboard=True,
        ),
    )
    plain = demote_method(method)
    assert plain is not None
    assert plain.reply_markup.keyboard[0][0].icon_custom_emoji_id is None


def test_demote_strips_custom_emoji_entities() -> None:
    from aiogram.methods import SendMessage
    from aiogram.types import MessageEntity

    method = SendMessage(
        chat_id=1,
        text="AB",
        entities=[
            MessageEntity(type="custom_emoji", offset=0, length=1, custom_emoji_id=PREMIUM_ID),
            MessageEntity(type="bold", offset=1, length=1),
        ],
    )
    plain = demote_method(method)
    assert plain is not None
    assert [e.type for e in plain.entities] == ["bold"]


def test_demote_returns_none_when_there_is_nothing_premium() -> None:
    """No premium content means the original error was about something else."""
    from aiogram.methods import SendMessage

    method = SendMessage(chat_id=1, text="just text")
    assert demote_method(method) is None


def test_demote_also_cleans_captions() -> None:
    from aiogram.methods import SendPhoto

    method = SendPhoto(
        chat_id=1,
        photo="file_id",
        caption=f'<tg-emoji emoji-id="{PREMIUM_ID}">🎉</tg-emoji> تمام',
    )
    plain = demote_method(method)
    assert plain is not None
    assert plain.caption == "🎉 تمام"
