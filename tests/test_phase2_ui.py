"""Phase-2 acceptance tests: styles, emoji registry, keyboards, pagination,
premium-emoji degradation, and the menu built from the registry.

These assert on the *payload* aiogram would send — that is the only thing under
our control. Whether a colour actually renders depends on the client version, and
`/ui` exists to check that by eye.
"""

from __future__ import annotations

import pytest
from aiogram.enums import ButtonStyle
from aiogram.types import ReplyKeyboardMarkup
from app.ui.emoji import MISSING, EmojiRegistry, looks_like_custom_emoji_rejection
from app.ui.kb import KB, ReplyKB, inline_button
from app.ui.styles import Style, resolve

PREMIUM_ID = "5368324170671202286"


def loaded_registry(
    mapping: dict[str, tuple[str | None, str]] | None = None, *, premium: bool = True
) -> EmojiRegistry:
    """Registry with a fixed slot mapping — no Redis, no database."""
    registry = EmojiRegistry(redis=None)
    registry._local = mapping if mapping is not None else {"ok": (PREMIUM_ID, "✅")}
    registry._premium_enabled = premium
    return registry


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------


def test_resolve_maps_intent_to_the_three_api_values() -> None:
    assert resolve(Style.PRIMARY) == ButtonStyle.PRIMARY
    assert resolve(Style.CONFIRM) == ButtonStyle.SUCCESS
    assert resolve(Style.DESTRUCTIVE) == ButtonStyle.DANGER


def test_neutral_style_is_omitted_entirely() -> None:
    """No style field at all, so the client picks its own default."""
    assert resolve(Style.NEUTRAL) is None
    assert resolve(None) is None


def test_resolve_accepts_plain_strings_and_ignores_junk() -> None:
    assert resolve("confirm") == ButtonStyle.SUCCESS
    assert resolve("chartreuse") is None


def test_every_style_member_is_mapped() -> None:
    """A new Style without a mapping would raise KeyError at render time."""
    for style in Style:
        resolve(style)


# ---------------------------------------------------------------------------
# EmojiRegistry
# ---------------------------------------------------------------------------


def test_premium_emoji_becomes_a_tg_emoji_tag_in_text() -> None:
    registry = loaded_registry()
    assert registry.text("ok") == f'<tg-emoji emoji-id="{PREMIUM_ID}">✅</tg-emoji>'


def test_fallback_is_kept_inside_the_tag() -> None:
    """Clients that cannot render the custom emoji still show something."""
    assert "✅</tg-emoji>" in loaded_registry().text("ok")


def test_text_is_plain_unicode_when_premium_is_off() -> None:
    registry = loaded_registry(premium=False)
    assert registry.text("ok") == "✅"
    assert "<tg-emoji" not in registry.text("ok")


def test_text_is_plain_unicode_when_no_id_is_set() -> None:
    registry = loaded_registry({"ok": (None, "✅")})
    assert registry.text("ok") == "✅"


def test_unknown_slot_is_visible_not_silent() -> None:
    registry = loaded_registry()
    assert registry.fallback("no_such_slot") == MISSING
    assert registry.custom_id("no_such_slot") is None


def test_custom_id_returns_none_while_premium_is_off() -> None:
    assert loaded_registry(premium=False).custom_id("ok") is None
    assert loaded_registry(premium=True).custom_id("ok") == PREMIUM_ID


def test_label_prefixes_the_emoji() -> None:
    assert loaded_registry(premium=False).label("ok", "سلام") == "✅ سلام"


@pytest.mark.parametrize(
    "message",
    [
        "Bad Request: CUSTOM_EMOJI_INVALID",
        "Bad Request: can't use custom emoji",
        "Bad Request: not enough rights to send custom emoji",
    ],
)
def test_rejection_detector_recognises_telegram_wording(message: str) -> None:
    assert looks_like_custom_emoji_rejection(message)


def test_rejection_detector_ignores_unrelated_errors() -> None:
    assert not looks_like_custom_emoji_rejection("Bad Request: message is not modified")
    assert not looks_like_custom_emoji_rejection("Bad Request: chat not found")


# ---------------------------------------------------------------------------
# Inline keyboards
# ---------------------------------------------------------------------------


def test_button_carries_style_and_premium_icon() -> None:
    button = inline_button(
        "برو",
        callback_data="go",
        style=Style.CONFIRM,
        emoji_slot="ok",
        registry=loaded_registry(),
    )
    assert button.style == ButtonStyle.SUCCESS
    assert button.icon_custom_emoji_id == PREMIUM_ID
    assert button.text == "برو", "with a premium icon the label stays clean"


def test_button_inlines_the_fallback_when_premium_is_unavailable() -> None:
    """The icon must not simply vanish — it moves into the label."""
    button = inline_button(
        "برو",
        callback_data="go",
        style=Style.CONFIRM,
        emoji_slot="ok",
        registry=loaded_registry(premium=False),
    )
    assert button.icon_custom_emoji_id is None
    assert button.text == "✅ برو"


def test_button_without_a_slot_has_no_icon() -> None:
    button = inline_button("برو", callback_data="go", registry=loaded_registry())
    assert button.icon_custom_emoji_id is None
    assert button.text == "برو"


def test_url_and_switch_inline_buttons_still_take_style_and_icon() -> None:
    registry = loaded_registry()
    url_button = inline_button(
        "سایت", url="https://example.com", style=Style.PRIMARY, emoji_slot="ok", registry=registry
    )
    assert url_button.url == "https://example.com"
    assert url_button.callback_data is None
    assert url_button.style == ButtonStyle.PRIMARY
    assert url_button.icon_custom_emoji_id == PREMIUM_ID

    switch = inline_button(
        "جست‌وجو", switch_inline_query_current_chat="q", style=Style.NEUTRAL, registry=registry
    )
    assert switch.switch_inline_query_current_chat == "q"
    assert switch.callback_data is None


def test_kb_serialises_style_only_when_set() -> None:
    markup = (
        KB(loaded_registry())
        .button("A", callback_data="a", style=Style.DESTRUCTIVE)
        .button("B", callback_data="b", style=Style.NEUTRAL)
        .adjust(2)
        .as_markup()
    )
    payload = markup.model_dump(exclude_none=True)
    first, second = payload["inline_keyboard"][0]
    assert first["style"] == "danger"
    assert "style" not in second, "NEUTRAL must not send a style field"


def test_kb_adjust_controls_row_widths() -> None:
    kb = KB(loaded_registry())
    for i in range(5):
        kb.button(str(i), callback_data=str(i))
    rows = kb.adjust(3, 2).as_markup().inline_keyboard
    assert [len(r) for r in rows] == [3, 2]


def test_kb_row_keeps_explicit_grouping() -> None:
    registry = loaded_registry()
    markup = (
        KB(registry)
        .row(
            inline_button("1", callback_data="1", registry=registry),
            inline_button("2", callback_data="2", registry=registry),
            inline_button("3", callback_data="3", registry=registry),
        )
        .row(inline_button("4", callback_data="4", registry=registry))
        .as_markup()
    )
    assert [len(r) for r in markup.inline_keyboard] == [3, 1]


def test_reply_keyboard_carries_style_and_icon() -> None:
    markup = (
        ReplyKB(loaded_registry())
        .button("شروع", style=Style.PRIMARY, emoji_slot="ok")
        .as_markup(placeholder="بنویس…")
    )
    assert isinstance(markup, ReplyKeyboardMarkup)
    button = markup.keyboard[0][0]
    assert button.style == ButtonStyle.PRIMARY
    assert button.icon_custom_emoji_id == PREMIUM_ID
    assert markup.resize_keyboard is True
    assert markup.input_field_placeholder == "بنویس…"


def test_reply_keyboard_falls_back_like_inline_does() -> None:
    markup = ReplyKB(loaded_registry(premium=False)).button("شروع", emoji_slot="ok").as_markup()
    button = markup.keyboard[0][0]
    assert button.icon_custom_emoji_id is None
    assert button.text == "✅ شروع"
