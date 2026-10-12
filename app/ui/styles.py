"""Semantic button styles.

Bot API 9.4 added a `style` field to InlineKeyboardButton and KeyboardButton, but
it accepts exactly three values: `primary` (blue), `success` (green) and
`danger` (red). There is no arbitrary colour.

Feature code therefore names an *intent* (`Style.CONFIRM`, `Style.DESTRUCTIVE`, …)
and this module maps it to whatever the API currently supports. If Telegram ever
adds more colours, only the mapping below changes.

Client caveat: styling renders only on Telegram clients released after
2026-02-09. Older clients show the button unstyled — no error, silent degradation.
"""

from __future__ import annotations

from enum import StrEnum

from aiogram.enums import ButtonStyle


class Style(StrEnum):
    """What a button *means*. Colour is derived, not chosen."""

    #: App default — no style field is sent at all.
    NEUTRAL = "neutral"

    #: The main action of a screen (blue).
    PRIMARY = "primary"

    #: Confirms, enables, succeeds (green).
    CONFIRM = "confirm"

    #: Deletes, cancels, disables, warns (red).
    DESTRUCTIVE = "destructive"


#: Intent -> Bot API value. `None` means "omit the field".
_TELEGRAM_STYLE: dict[Style, ButtonStyle | None] = {
    Style.NEUTRAL: None,
    Style.PRIMARY: ButtonStyle.PRIMARY,
    Style.CONFIRM: ButtonStyle.SUCCESS,
    Style.DESTRUCTIVE: ButtonStyle.DANGER,
}


def resolve(style: Style | str | None) -> ButtonStyle | None:
    """Map a semantic style to the Bot API value, or None to omit it."""
    if style is None:
        return None
    if not isinstance(style, Style):
        try:
            style = Style(style)
        except ValueError:
            return None
    return _TELEGRAM_STYLE[style]
