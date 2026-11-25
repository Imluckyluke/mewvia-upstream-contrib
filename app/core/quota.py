"""Daily per-user conversion quota.

The admin sets `daily_quota` (0 = unlimited). Features call
:func:`check_daily_quota` before starting a heavy job; when the user already
reached the limit today, the helper answers the quota notice and the caller
returns early. Only successful-today conversions count — failures and
yesterday's work never punish the user.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logging import get_logger
from app.core.settings_store import get_settings_store
from app.core.units import fa_digits
from app.db.models.conversion import Conversion
from app.db.models.quota import QuotaLease

log = get_logger(__name__)


async def remaining_quota(session: AsyncSession, user_id: int) -> int | None:
    """Quota left for today, or None when unlimited (quota = 0 / unset)."""
    try:
        store = get_settings_store()
        quota = await store.get_int("daily_quota", 0)
    except Exception:
        return None
    if quota <= 0:
        return None
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    used = (
        await session.execute(
            select(func.count(Conversion.id)).where(
                Conversion.telegram_id == user_id,
                Conversion.status == "success",
                Conversion.created_at >= day_start,
            )
        )
    ).scalar_one()
    return max(0, quota - int(used))


async def check_daily_quota(
    session: AsyncSession,
    user_id: int,
    target: Message | CallbackQuery,
) -> bool:
    """True when the user may proceed; answers the notice and returns False at the cap."""
    try:
        store = get_settings_store()
        quota = await store.get_int("daily_quota", 0)
    except RuntimeError:
        return True
    if quota <= 0 or session.info.get("quota_lease"):
        return True
    # Serialize admission, not processing. A crashed job becomes eligible again
    # when its bounded processing deadline plus a cleanup grace period expires.
    await session.execute(select(func.pg_advisory_xact_lock(user_id)))
    now = datetime.now(UTC)
    await session.execute(delete(QuotaLease).where(QuotaLease.expires_at <= now))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    used = await session.scalar(
        select(func.count(Conversion.id)).where(
            Conversion.telegram_id == user_id,
            Conversion.status.in_(("success", "partial")),
            Conversion.created_at >= start,
        )
    )
    left = quota - int(used or 0)
    pending = await session.scalar(
        select(func.count()).select_from(QuotaLease).where(QuotaLease.user_id == user_id)
    )
    if left is not None and left > (pending or 0):
        token = uuid4().hex
        session.add(
            QuotaLease(
                token=token,
                user_id=user_id,
                expires_at=now + timedelta(seconds=max(1, get_settings().job_timeout) + 60),
            )
        )
        await session.commit()
        session.info["quota_lease"] = token
        return True
    await session.commit()
    notice = (
        f"⚠️ <b>سهمیه روزانه‌ات تموم شده</b>\n\n"
        f"سقف امروز: <code>{fa_digits(str(quota))}</code> تبدیل — فردا دوباره بیا! 🎵"
    )
    log.info("daily_quota_exhausted", user_id=user_id, quota=quota)
    if isinstance(target, CallbackQuery):
        await target.answer("سهمیه روزانه‌ات تموم شده! فردا دوباره بیا.", show_alert=True)
    elif isinstance(target, Message):
        await target.answer(notice)
    return False


async def release_quota(session: AsyncSession) -> None:
    token = session.info.pop("quota_lease", None)
    if token:
        await session.execute(delete(QuotaLease).where(QuotaLease.token == token))
