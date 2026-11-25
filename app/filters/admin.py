"""Admin gate.

Authority comes from ADMIN_IDS in the environment, not from a database column:
a compromised or mis-edited database must not be able to grant admin rights, and
the list has to work before any migration has run.
"""

from __future__ import annotations

from aiogram.enums import ChatType
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from app.config import Settings


class IsAdmin(BaseFilter):
    async def __call__(
        self,
        event: TelegramObject,
        event_from_user: User | None = None,
        settings: Settings | None = None,
        **_: object,
    ) -> bool:
        if event_from_user is None or settings is None:
            return False
        msg = event.message if isinstance(event, CallbackQuery) else event
        return (
            isinstance(msg, Message)
            and msg.chat.type == ChatType.PRIVATE
            and msg.chat.id == event_from_user.id
            and settings.is_admin(event_from_user.id)
        )
