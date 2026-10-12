"""Button labels and their emoji decoration.

A button's visible text is not always the text the code asked for: when no
premium emoji is available the unicode fallback is prefixed to the label, so
`"شروع مجدد"` reaches Telegram as `"🔄 شروع مجدد"`.

That matters for the *reply* keyboard, because a tap on one arrives back as an
ordinary text message. A handler matching the undecorated string would never
fire. Decoration and its inverse therefore live here, in one place, and both the
keyboard builder and the reply-button filter use them.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable

from aiogram.filters import Filter
from aiogram.types import Message

#: Unicode categories a real label may start with: letters and digits.
#: Everything before the first one of these is decoration.
_LABEL_START = frozenset({"Ll", "Lu", "Lt", "Lm", "Lo", "Nd", "Nl", "No"})

#: Categories that make a string "emoji-ish": pictographs, modifier symbols and
#: enclosing marks (keycaps). At least one is required, which is what separates
#: an emoji from "123" or "!!!".
_SYMBOLIC = frozenset({"So", "Sk", "Me"})

#: A `fallback` column holds 16 characters; a ZWJ family sequence needs 7.
MAX_FALLBACK_LEN = 16


def decorate(icon: str, text: str) -> str:
    """`("🔄", "شروع مجدد")` -> `"🔄 شروع مجدد"`. Idempotent."""
    if not icon or text.startswith(icon):
        return text
    return f"{icon} {text}"


def strip_decoration(label: str) -> str:
    """Drop a leading emoji from a button label, leaving the words.

    Used to recognise a reply-keyboard tap regardless of whether the icon was
    rendered as a premium emoji (label untouched) or inlined as unicode.
    """
    for index, char in enumerate(label):
        if unicodedata.category(char) in _LABEL_START:
            return label[index:].strip()
    return label.strip()


def matches_label(text: str, target: str) -> bool:
    """True if `text` stripped of its leading decoration matches `target`."""
    return strip_decoration(text) == strip_decoration(target)


class ReplyTextFilter(Filter):
    """Matches a message's text against an expected reply-keyboard label.

    Handles both decorated ("🔄 شروع مجدد") and undecorated ("شروع مجدد") text.
    """

    def __init__(self, target_or_getter: str | Callable[[], str]) -> None:
        self._target_or_getter = target_or_getter

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        target = (
            self._target_or_getter() if callable(self._target_or_getter) else self._target_or_getter
        )
        return matches_label(message.text, target)


def looks_like_unicode_emoji(text: str) -> bool:
    """True for `✅`, `👨‍👩‍👧‍👦`, `1️⃣`; false for words, `123`, `!!!`.

    Deliberately permissive about *which* emoji: the point is only to tell an
    emoji apart from a sentence, so an admin who sends a plain emoji gets it
    stored as the slot's fallback instead of an error.
    """
    stripped = text.strip()
    if not stripped or len(stripped) > MAX_FALLBACK_LEN:
        return False
    if any(char.isspace() or unicodedata.category(char).startswith("L") for char in stripped):
        return False
    return any(unicodedata.category(char) in _SYMBOLIC for char in stripped)
