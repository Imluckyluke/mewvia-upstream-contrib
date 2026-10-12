"""Runtime settings queries."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting


class SettingRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str) -> str | None:
        return (
            await self.session.execute(select(Setting.value).where(Setting.key == key))
        ).scalar_one_or_none()

    async def all(self) -> Sequence[Setting]:
        stmt = select(Setting).order_by(Setting.key)
        return (await self.session.execute(stmt)).scalars().all()

    async def as_dict(self) -> dict[str, str]:
        stmt = select(Setting.key, Setting.value)
        return {k: v for k, v in (await self.session.execute(stmt)).all()}

    async def set(
        self, key: str, value: str, *, updated_by: int | None = None, description: str | None = None
    ) -> None:
        stmt = insert(Setting).values(
            key=key, value=value, updated_by=updated_by, description=description
        )
        update: dict[str, object] = {"value": value, "updated_by": updated_by}
        if description is not None:
            update["description"] = description
        await self.session.execute(
            stmt.on_conflict_do_update(index_elements=[Setting.key], set_=update)
        )

    async def ensure(self, key: str, default: str, description: str = "") -> str:
        """Insert the default if absent; return the effective value.

        Used by the seed step, and safe to call repeatedly — a value an admin has
        already changed is never overwritten.
        """
        stmt = (
            insert(Setting)
            .values(key=key, value=default, description=description or None)
            .on_conflict_do_nothing(index_elements=[Setting.key])
        )
        await self.session.execute(stmt)
        current = await self.get(key)
        return current if current is not None else default
