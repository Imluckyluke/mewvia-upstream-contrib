"""Instagram cookie repository queries."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.instagram_cookie import InstagramCookie


class InstagramCookieRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, cookie_id: int) -> InstagramCookie | None:
        return await self.session.get(InstagramCookie, cookie_id)

    async def all(self) -> Sequence[InstagramCookie]:
        stmt = select(InstagramCookie).order_by(InstagramCookie.id)
        return (await self.session.execute(stmt)).scalars().all()

    async def get_active_cookies(self) -> Sequence[InstagramCookie]:
        stmt = (
            select(InstagramCookie)
            .where(InstagramCookie.is_active.is_(True))
            .order_by(InstagramCookie.id)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_active_valid_cookies(self) -> Sequence[InstagramCookie]:
        """Return active cookies that are not marked as permanently invalid."""
        stmt = (
            select(InstagramCookie)
            .where(
                InstagramCookie.is_active.is_(True),
                InstagramCookie.status != "invalid",
            )
            .order_by(
                InstagramCookie.last_used_at.asc().nulls_first(),
                InstagramCookie.use_count.asc(),
            )
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_next_cookie(self) -> InstagramCookie | None:
        """Select next available cookie using round-robin (least recently used).

        Only returns cookies that are active and not marked as permanently invalid.
        If no valid active cookie exists, returns None so callers can use
        anonymous Chrome-impersonated extraction.
        """
        candidates = await self.get_active_valid_cookies()
        if not candidates:
            # Check for untested active cookies (never return "invalid" ones)
            stmt = (
                select(InstagramCookie)
                .where(
                    InstagramCookie.is_active.is_(True),
                    InstagramCookie.status != "invalid",
                )
                .order_by(
                    InstagramCookie.last_used_at.asc().nulls_first(),
                    InstagramCookie.use_count.asc(),
                )
            )
            return (await self.session.execute(stmt)).scalars().first()
        return candidates[0]

    async def add(
        self,
        *,
        label: str,
        cookie_text: str,
        username: str | None = None,
        account_id: str | None = None,
        status: str = "untested",
    ) -> InstagramCookie:
        cookie = InstagramCookie(
            label=label,
            cookie_text=cookie_text,
            username=username,
            account_id=account_id,
            status=status,
            is_active=True,
        )
        self.session.add(cookie)
        await self.session.flush()
        return cookie

    async def set_active(self, cookie_id: int, is_active: bool) -> bool:
        cookie = await self.get_by_id(cookie_id)
        if cookie is None:
            return False
        cookie.is_active = is_active
        await self.session.flush()
        return True

    async def set_status(
        self,
        cookie_id: int,
        status: str,
        error_message: str | None = None,
    ) -> bool:
        cookie = await self.get_by_id(cookie_id)
        if cookie is None:
            return False
        cookie.status = status
        cookie.error_message = error_message
        cookie.last_checked_at = datetime.now(UTC)
        await self.session.flush()
        return True

    async def update_label(self, cookie_id: int, label: str) -> bool:
        cookie = await self.get_by_id(cookie_id)
        if cookie is None:
            return False
        cookie.label = label
        await self.session.flush()
        return True

    async def record_usage(
        self,
        cookie_id: int,
        *,
        success: bool,
        error: str | None = None,
    ) -> None:
        cookie = await self.get_by_id(cookie_id)
        if cookie is None:
            return
        cookie.last_used_at = datetime.now(UTC)
        if success:
            cookie.use_count += 1
            cookie.status = "valid"
            cookie.error_message = None
        else:
            cookie.fail_count += 1
            if error:
                cookie.error_message = error[:250]
            if cookie.fail_count >= 3:
                cookie.status = "invalid"
        await self.session.flush()

    async def delete(self, cookie_id: int) -> bool:
        cookie = await self.get_by_id(cookie_id)
        if cookie is None:
            return False
        await self.session.delete(cookie)
        await self.session.flush()
        return True

    async def count(self) -> int:
        stmt = select(func.count(InstagramCookie.id))
        return (await self.session.execute(stmt)).scalar_one()
