"""Emoji slot queries."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EmojiSlot


class EmojiRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, slot: str) -> EmojiSlot | None:
        return await self.session.get(EmojiSlot, slot)

    async def all(self) -> Sequence[EmojiSlot]:
        stmt = select(EmojiSlot).order_by(EmojiSlot.sort_order, EmojiSlot.slot)
        return (await self.session.execute(stmt)).scalars().all()

    async def as_mapping(self) -> dict[str, tuple[str | None, str]]:
        """slot -> (custom_emoji_id, fallback). What the UI layer caches."""
        stmt = select(EmojiSlot.slot, EmojiSlot.custom_emoji_id, EmojiSlot.fallback)
        rows = (await self.session.execute(stmt)).all()
        return {slot: (cid, fb) for slot, cid, fb in rows}

    async def page(self, *, offset: int, limit: int) -> Sequence[EmojiSlot]:
        stmt = (
            select(EmojiSlot)
            .order_by(EmojiSlot.sort_order, EmojiSlot.slot)
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def count(self) -> int:
        return (await self.session.execute(select(func.count(EmojiSlot.slot)))).scalar_one()

    async def set_custom_emoji(
        self, slot: str, custom_emoji_id: str | None, *, updated_by: int | None = None
    ) -> bool:
        """Attach or detach a premium emoji. Returns False if the slot is unknown."""
        row = await self.get(slot)
        if row is None:
            return False
        row.custom_emoji_id = custom_emoji_id
        row.updated_by = updated_by
        return True

    async def clear_all(self, *, updated_by: int | None = None) -> int:
        """Drop every premium emoji, keeping the slots and their fallbacks.

        This is the "حذف همگانی همه ایموجی‌ها" button: it must not delete slots,
        or the UI would lose its icon names.
        """
        rows = await self.all()
        touched = 0
        for row in rows:
            if row.custom_emoji_id is not None:
                row.custom_emoji_id = None
                row.updated_by = updated_by
                touched += 1
        return touched

    async def ensure(self, slot: str, title: str, fallback: str, sort_order: int) -> None:
        """Seed a slot. Never overwrites an admin's custom_emoji_id."""
        stmt = (
            insert(EmojiSlot)
            .values(slot=slot, title=title, fallback=fallback, sort_order=sort_order)
            .on_conflict_do_update(
                index_elements=[EmojiSlot.slot],
                # Title/fallback/order are ours to evolve in code; the premium id
                # belongs to the admin and is left alone.
                set_={"title": title, "fallback": fallback, "sort_order": sort_order},
            )
        )
        await self.session.execute(stmt)
