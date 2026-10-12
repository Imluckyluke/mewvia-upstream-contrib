"""Session middleware that makes premium emoji safe to use everywhere.

Premium emoji are only allowed when the bot owner holds Telegram Premium, and
never in channels. Rather than guarding every call site, this middleware sits on
the Bot's request chain: if Telegram refuses a request because of a custom emoji,
it strips the premium emoji out and retries once.

Consequences:

* a lapsed Premium subscription degrades the UI instead of breaking every message;
* the same screen code works in a private chat and in a channel broadcast;
* the global switch is turned off after the first rejection, so the retry happens
  once rather than on every subsequent message.
"""

from __future__ import annotations

import re
from typing import Any

from aiogram import Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import Response, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

from app.core.logging import get_logger
from app.ui.emoji import looks_like_custom_emoji_rejection

log = get_logger(__name__)

#: `<tg-emoji emoji-id="123">✅</tg-emoji>` -> `✅`
TG_EMOJI_TAG = re.compile(r"<tg-emoji\s+emoji-id=\"[^\"]*\"\s*>(.*?)</tg-emoji>", re.DOTALL)

#: Fields that may carry a `<tg-emoji>` tag.
TEXT_FIELDS = ("text", "caption", "question", "explanation", "description")


def strip_tg_emoji(text: str) -> str:
    """Replace every `<tg-emoji>` tag with the unicode character it wraps."""
    return TG_EMOJI_TAG.sub(r"\1", text)


def _demote_button(button: Any) -> Any:
    """Strip a button's premium icon, moving its fallback into the label.

    Without the label change the icon would simply disappear on the retry, which
    reads as a rendering bug rather than a deliberate downgrade.
    """
    icon_id = getattr(button, "icon_custom_emoji_id", None)
    if icon_id is None:
        return button

    updates: dict[str, Any] = {"icon_custom_emoji_id": None}
    fallback = _fallback_for(icon_id)
    if fallback and not button.text.startswith(fallback):
        updates["text"] = f"{fallback} {button.text}"
    return button.model_copy(update=updates)


def _fallback_for(custom_emoji_id: str) -> str | None:
    try:
        from app.ui.emoji import get_emoji_registry

        return get_emoji_registry().fallback_for_id(custom_emoji_id)
    except Exception:  # pragma: no cover - registry not initialised (tests)
        return None


def _strip_markup(markup: Any) -> Any:
    """Remove `icon_custom_emoji_id` from every button, keeping `style`."""
    if isinstance(markup, InlineKeyboardMarkup):
        inline_rows = [[_demote_button(b) for b in row] for row in markup.inline_keyboard]
        return markup.model_copy(update={"inline_keyboard": inline_rows})
    if isinstance(markup, ReplyKeyboardMarkup):
        reply_rows = [[_demote_button(b) for b in row] for row in markup.keyboard]
        return markup.model_copy(update={"keyboard": reply_rows})
    return markup


def demote_method(method: TelegramMethod[TelegramType]) -> TelegramMethod[TelegramType] | None:
    """A copy of `method` with all premium emoji removed, or None if it had none."""
    updates: dict[str, Any] = {}

    for field in TEXT_FIELDS:
        value = getattr(method, field, None)
        if isinstance(value, str) and "<tg-emoji" in value:
            updates[field] = strip_tg_emoji(value)

    # entities/caption_entities may carry custom_emoji entities when a caller
    # builds them explicitly instead of using HTML.
    for field in ("entities", "caption_entities"):
        value = getattr(method, field, None)
        if value:
            kept = [e for e in value if getattr(e, "type", None) != "custom_emoji"]
            if len(kept) != len(value):
                updates[field] = kept or None

    markup = getattr(method, "reply_markup", None)
    if markup is not None:
        stripped = _strip_markup(markup)
        if stripped is not markup:
            updates["reply_markup"] = stripped

    if not updates:
        return None
    return method.model_copy(update=updates)


class PremiumEmojiGuard(BaseRequestMiddleware):
    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as exc:
            if not looks_like_custom_emoji_rejection(exc.message):
                raise

            plain = demote_method(method)
            if plain is None:
                raise

            log.warning(
                "custom_emoji_rejected_retrying_plain",
                method=type(method).__name__,
                telegram_message=exc.message,
            )
            await self._disable_premium(exc.message)
            return await make_request(bot, plain)

    @staticmethod
    async def _disable_premium(reason: str) -> None:
        """Flip the switch off so the next message does not repeat the round trip."""
        try:
            from app.ui.emoji import get_emoji_registry

            await get_emoji_registry().disable_premium(reason)
        except Exception as exc:  # pragma: no cover - must never mask the retry
            log.warning("premium_flag_update_failed", error=str(exc))
