"""Daily per-user conversion quota.

The admin sets `daily_quota` (0 = unlimited). Features call
:func:`check_daily_quota` before starting a heavy job; when the user already
reached the limit today, the helper answers the quota notice and the caller
returns early. Only successful-today conversions count — failures and
yesterday's work never punish the user.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings_store import get_settings_store
from app.core.units import fa_digits
from app.db.models.conversion import Conversion

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
    left = await remaining_quota(session, user_id)
    if left is None or left > 0:
        return True
    try:
        store = get_settings_store()
        quota = await store.get_int("daily_quota", 0)
    except Exception:
        quota = 0
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
