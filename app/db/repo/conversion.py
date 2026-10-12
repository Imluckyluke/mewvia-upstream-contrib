"""Conversion repository queries."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.conversion import Conversion
from app.db.models.user import User


class ConversionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def log_conversion(
        self,
        *,
        telegram_id: int,
        source_kind: str,
        target_id: str,
        in_bytes: int = 0,
        out_bytes: int = 0,
        duration_ms: int = 0,
        status: str = "success",
        error_code: str | None = None,
    ) -> Conversion:
        record = Conversion(
            telegram_id=telegram_id,
            source_kind=source_kind,
            target_id=target_id,
            in_bytes=in_bytes,
            out_bytes=out_bytes,
            duration_ms=duration_ms,
            status=status,
            error_code=error_code,
        )
        self.session.add(record)
        if status == "success":
            # The "real user" flag for admin stats. Flush first so a brand-new
            # user row (upserted earlier in this same transaction but not yet
            # flushed) is visible to the UPDATE below — otherwise a first-ever
            # conversion would silently miss the flag.
            await self.session.flush()
            await self.session.execute(
                update(User).where(User.telegram_id == telegram_id).values(has_used_bot=True)
            )
        return record

    async def count_total(self) -> int:
        return (await self.session.execute(select(func.count(Conversion.id)))).scalar_one()

    async def count_successful(self) -> int:
        stmt = select(func.count(Conversion.id)).where(Conversion.status == "success")
        return (await self.session.execute(stmt)).scalar_one()
