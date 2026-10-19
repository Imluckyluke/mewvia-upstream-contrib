"""Upserts the sender on real bot interactions and exposes them as `db_user`.

Only genuine interactions reach the upsert:

* private chats — any update (the user came to the bot on purpose);
* groups — only ``/``-commands and callback-button taps. Plain group chatter
  (someone saying «سلام بچه‌ها» in a group where the bot happens to sit)
  is NOT a bot user and must not inflate member/user statistics.

Runs after DbSessionMiddleware (it needs the session) and before everything that
inspects the user. Banned users are dropped here: returning without calling the
handler stops the update, so no feature has to remember to check.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from app.config import Settings
from app.core.logging import get_logger
from app.db.repo import UserRepo

log = get_logger(__name__)


def is_real_interaction(event: TelegramObject) -> bool:
    """True if this update is a deliberate interaction with the bot."""
    try:
        inner = getattr(event, "event", None) or event
    except Exception:
        # Empty/unknown update shapes carry no interaction.
        return False

    if isinstance(inner, CallbackQuery):
        # A button tap is always deliberate — in any chat type.
        return True

    if isinstance(inner, Message):
        chat_type = getattr(inner.chat, "type", None)
        if chat_type in (ChatType.GROUP, ChatType.SUPERGROUP):
            # Group chatter is noise; only Mewvia /commands and media URLs count.
            text = (inner.text or inner.caption or "").strip()
            if text.startswith("/"):
                cmd_name = text.split()[0][1:].lower().split("@")[0]
                if cmd_name in {
                    "media",
                    "mewvia",
                    "dl",
                    "shazam",
                    "findsong",
                    "songid",
                    "music",
                    "edit",
                    "tools",
                    "ابزار",
                    "پنل",
                }:
                    return True
                return False

            if "@mewvia_bot" in text.lower():
                return True

            from app.media.downloader import extract_first_url, is_supported_media_url

            url = extract_first_url(text)
            if url and is_supported_media_url(url):
                return True
            return False
        # Private chats (and channels edge-cases) always count.
        return True

    # chat_member / my_chat_member / join requests / reactions … — not usage.
    return False


class UserTrackingMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: User | None = data.get("event_from_user")

        # Updates with no sender (channel posts, poll answers from anonymous
        # sources) still reach handlers, just without a db_user.
        if tg_user is None or tg_user.is_bot:
            data["db_user"] = None
            data["is_admin"] = False
            return await handler(event, data)

        data["is_admin"] = self.settings.is_admin(tg_user.id)

        if not is_real_interaction(event):
            # Group noise: look up the user for the ban check but do NOT
            # create/touch the row — no updates_seen bump, no last_seen_at
            # refresh, so member stats stay clean.
            repo: UserRepo = data["user_repo"]
            db_user = await repo.get(tg_user.id)
            data["db_user"] = db_user
            if db_user is not None and db_user.is_banned and not data["is_admin"]:
                log.info("banned_user_dropped", user_id=tg_user.id)
                return None
            return await handler(event, data)

        repo = data["user_repo"]
        db_user = await repo.upsert_from_telegram(tg_user)

        data["db_user"] = db_user

        if db_user.is_banned and not data["is_admin"]:
            log.info("banned_user_dropped", user_id=tg_user.id)
            return None

        return await handler(event, data)
