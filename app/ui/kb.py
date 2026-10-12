"""Keyboard builders.

Everything the bot shows goes through `KB` (inline) or `ReplyKB` (normal
keyboard), so colour and premium-emoji handling are applied once here rather than
being remembered at every call site.

Built on aiogram's own InlineKeyboardBuilder / ReplyKeyboardBuilder rather than
assembling markup dicts by hand.
"""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from app.ui.emoji import EmojiRegistry
from app.ui.labels import decorate, strip_decoration
from app.ui.styles import Style, resolve


def inline_button(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    switch_inline_query_current_chat: str | None = None,
    style: Style | str | None = Style.NEUTRAL,
    emoji_slot: str = "",
    registry: EmojiRegistry | None = None,
) -> InlineKeyboardButton:
    """One inline button.

    `emoji_slot` becomes `icon_custom_emoji_id` when a premium emoji is available;
    otherwise the unicode fallback is prefixed to the label, so the button always
    carries its icon in some form.
    """
    icon_id: str | None = None
    clean_text = strip_decoration(text) if emoji_slot else text
    label = clean_text

    if emoji_slot and registry is not None:
        icon_id = registry.custom_id(emoji_slot)
        if icon_id is None:
            label = decorate(registry.fallback(emoji_slot), clean_text)

    # Exactly one of url / callback_data / … defines the button type; style and
    # icon_custom_emoji_id are decorations and exempt from that rule.
    if url is not None:
        return InlineKeyboardButton(
            text=label, url=url, style=resolve(style), icon_custom_emoji_id=icon_id
        )
    if switch_inline_query_current_chat is not None:
        return InlineKeyboardButton(
            text=label,
            switch_inline_query_current_chat=switch_inline_query_current_chat,
            style=resolve(style),
            icon_custom_emoji_id=icon_id,
        )
    return InlineKeyboardButton(
        text=label,
        callback_data=callback_data or "noop",
        style=resolve(style),
        icon_custom_emoji_id=icon_id,
    )


class KB:
    """Fluent inline-keyboard builder."""

    def __init__(self, registry: EmojiRegistry | None = None) -> None:
        self._builder = InlineKeyboardBuilder()
        self._registry = registry
        self._pending_widths: list[int] = []

    def button(
        self,
        text: str,
        *,
        callback_data: str | None = None,
        url: str | None = None,
        switch_inline_query_current_chat: str | None = None,
        style: Style | str | None = Style.NEUTRAL,
        emoji_slot: str = "",
    ) -> KB:
        self._builder.add(
            inline_button(
                text,
                callback_data=callback_data,
                url=url,
                switch_inline_query_current_chat=switch_inline_query_current_chat,
                style=style,
                emoji_slot=emoji_slot,
                registry=self._registry,
            )
        )
        return self

    def row(
        self,
        *buttons: InlineKeyboardButton,
        width: int | None = None,
    ) -> KB:
        """Add an explicit row of already-built buttons."""
        self._builder.row(*buttons, width=width or len(buttons) or 1)
        return self

    def adjust(self, *sizes: int) -> KB:
        """Row widths, e.g. `.adjust(3, 3, 1)`."""
        self._builder.adjust(*sizes)
        return self

    def as_markup(self, **kwargs: object) -> InlineKeyboardMarkup:
        return self._builder.as_markup(**kwargs)


class ReplyKB:
    """Fluent builder for the normal (reply) keyboard."""

    def __init__(self, registry: EmojiRegistry | None = None) -> None:
        self._builder = ReplyKeyboardBuilder()
        self._registry = registry

    def button(
        self,
        text: str,
        *,
        style: Style | str | None = Style.NEUTRAL,
        emoji_slot: str = "",
        request_contact: bool | None = None,
        request_location: bool | None = None,
    ) -> ReplyKB:
        icon_id: str | None = None
        clean_text = strip_decoration(text) if emoji_slot else text
        label = clean_text
        if emoji_slot and self._registry is not None:
            icon_id = self._registry.custom_id(emoji_slot)
            if icon_id is None:
                label = decorate(self._registry.fallback(emoji_slot), clean_text)

        self._builder.add(
            KeyboardButton(
                text=label,
                style=resolve(style),
                icon_custom_emoji_id=icon_id,
                request_contact=request_contact,
                request_location=request_location,
            )
        )
        return self

    def adjust(self, *sizes: int) -> ReplyKB:
        self._builder.adjust(*sizes)
        return self

    def as_markup(
        self, *, resize: bool = True, placeholder: str | None = None
    ) -> ReplyKeyboardMarkup:
        return self._builder.as_markup(
            resize_keyboard=resize,
            input_field_placeholder=placeholder,
        )
