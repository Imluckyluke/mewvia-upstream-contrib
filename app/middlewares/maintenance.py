"""Maintenance-mode middleware.

When the admin flips the `maintenance` runtime switch, ordinary users get a
short notice instead of service; admins keep full access so they can verify
the bot before reopening it. The optional `maintenance_message` setting
overrides the default Persian notice.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.core.logging import get_logger
from app.ui.texts import t

log = get_logger(__name__)


class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from app.core.settings_store import get_settings_store

        is_admin = bool(data.get("is_admin", False))
        if is_admin:
            return await handler(event, data)

        try:
            store = get_settings_store()
            in_maintenance = await store.get_bool("maintenance", False)
        except Exception:
            # A settings outage must not take the bot down.
            return await handler(event, data)

        if not in_maintenance:
            return await handler(event, data)

        try:
            custom = await store.get("maintenance_message", "")
        except Exception:
            custom = ""
        notice = custom.strip() or t("error.maintenance")

        try:
            inner = getattr(event, "event", None) or event
        except Exception:
            inner = event

        log.info("maintenance_blocked", update_kind=type(inner).__name__)
        if isinstance(inner, CallbackQuery):
            await inner.answer(notice, show_alert=True)
        elif isinstance(inner, Message):
            # In groups, stay silent during maintenance so chat is never disturbed
            if inner.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                return None
            await inner.answer(notice)
        return None
