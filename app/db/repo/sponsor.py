"""Sponsor channel repository queries."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.sponsor_channel import SponsorChannel


class SponsorRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, channel_db_id: int) -> SponsorChannel | None:
        return await self.session.get(SponsorChannel, channel_db_id)

    async def get_by_channel_id(self, channel_id: int) -> SponsorChannel | None:
        stmt = select(SponsorChannel).where(SponsorChannel.channel_id == channel_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_active_channels(self) -> Sequence[SponsorChannel]:
        stmt = (
            select(SponsorChannel)
            .where(SponsorChannel.is_active.is_(True))
            .order_by(SponsorChannel.id)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def all(self) -> Sequence[SponsorChannel]:
        stmt = select(SponsorChannel).order_by(SponsorChannel.id)
        return (await self.session.execute(stmt)).scalars().all()

    async def add_or_update(
        self,
        channel_id: int,
        title: str,
        username: str | None = None,
        invite_link: str | None = None,
        is_active: bool = True,
    ) -> SponsorChannel:
        stmt = (
            insert(SponsorChannel)
            .values(
                channel_id=channel_id,
                title=title,
                username=username,
                invite_link=invite_link,
                is_active=is_active,
            )
            .on_conflict_do_update(
                index_elements=[SponsorChannel.channel_id],
                set_={
                    "title": title,
                    "username": username,
                    "invite_link": invite_link,
                    "is_active": is_active,
                },
            )
            .returning(SponsorChannel)
        )
        result = await self.session.execute(stmt, execution_options={"populate_existing": True})
        return result.scalar_one()

    async def set_active(self, channel_db_id: int, is_active: bool) -> bool:
        channel = await self.get_by_id(channel_db_id)
        if channel is None:
            return False
        channel.is_active = is_active
        return True

    async def delete(self, channel_db_id: int) -> bool:
        channel = await self.session.get(SponsorChannel, channel_db_id)
        if channel is None:
            return False
        await self.session.delete(channel)
        await self.session.flush()
        return True

    async def count(self) -> int:
        stmt = select(func.count(SponsorChannel.id))
        return (await self.session.execute(stmt)).scalar_one()

    async def list_page(self, *, offset: int = 0, limit: int = 10) -> Sequence[SponsorChannel]:
        stmt = select(SponsorChannel).order_by(SponsorChannel.id).offset(offset).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()
