"""Sticker set repository queries."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.sticker_set import StickerSet


class StickerSetRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_name(self, name: str) -> StickerSet | None:
        return (
            await self.session.execute(select(StickerSet).where(StickerSet.name == name))
        ).scalar_one_or_none()

    async def get_user_sets(self, telegram_id: int) -> Sequence[StickerSet]:
        stmt = (
            select(StickerSet)
            .where(StickerSet.telegram_id == telegram_id)
            .order_by(StickerSet.updated_at.desc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def create_set(
        self, telegram_id: int, name: str, title: str, sticker_type: str = "regular"
    ) -> StickerSet:
        s_set = StickerSet(
            telegram_id=telegram_id,
            name=name,
            title=title,
            sticker_type=sticker_type,
            stickers_count=1,
        )
        self.session.add(s_set)
        return s_set

    async def increment_count(self, name: str) -> None:
        s_set = await self.get_by_name(name)
        if s_set:
            s_set.stickers_count += 1

    async def delete_set(self, name: str) -> bool:
        s_set = await self.get_by_name(name)
        if s_set:
            await self.session.delete(s_set)
            return True
        return False
