"""Admin gate.

Authority comes from ADMIN_IDS in the environment, not from a database column:
a compromised or mis-edited database must not be able to grant admin rights, and
the list has to work before any migration has run.
"""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject, User

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
        return settings.is_admin(event_from_user.id)
