"""Bound media handlers before fetching their Telegram input or allocating scratch space."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import Settings


class MediaAdmission(BaseMiddleware):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.slots = asyncio.Semaphore(max(1, settings.max_concurrent_jobs))
        self.users: set[int] = set()

    async def __call__(
        self, handler: Callable[..., Awaitable[Any]], event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)) or event.from_user is None:
            return await handler(event, data)
        uid = event.from_user.id
        if uid in self.users or len(self.users) >= max(1, self.settings.max_concurrent_jobs) * 2:
            await event.answer("صف پردازش پر است؛ کمی بعد دوباره تلاش کنید.")
            return None
        self.users.add(uid)
        try:
            async with asyncio.timeout(self.settings.job_timeout), self.slots:
                self.settings.tmp_dir.mkdir(parents=True, exist_ok=True)
                space = await asyncio.to_thread(shutil.disk_usage, self.settings.tmp_dir)
                if space.free < self.settings.media_max_bytes:
                    await event.answer("فضای کافی برای پردازش وجود ندارد.")
                    return None
                session = data.get("session")
                if session is not None and session.in_transaction():
                    await session.commit()
                return await handler(event, data)
        finally:
            self.users.discard(uid)
