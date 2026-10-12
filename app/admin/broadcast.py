"""Admin broadcast message engine."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.user import User

log = get_logger(__name__)


@dataclass
class BroadcastResult:
    total: int = 0
    successful: int = 0
    failed: int = 0
    blocked: int = 0


async def send_broadcast(
    bot: Bot,
    session: AsyncSession,
    source_message: Message,
    *,
    rate_limit: float = 0.04,  # ~25 messages / sec
) -> BroadcastResult:
    """Broadcast source_message to all active users."""
    stmt = (
        select(User)
        .where(User.is_banned.is_(False), User.has_blocked_bot.is_(False))
        .order_by(User.telegram_id.asc())
    )
    users = (await session.execute(stmt)).scalars().all()

    result = BroadcastResult(total=len(users))

    for user in users:
        try:
            await bot.copy_message(
                chat_id=user.telegram_id,
                from_chat_id=source_message.chat.id,
                message_id=source_message.message_id,
            )
            result.successful += 1
        except TelegramRetryAfter as exc:
            log.warning("broadcast_rate_limited", retry_after=exc.retry_after)
            await asyncio.sleep(exc.retry_after)
            try:
                await bot.copy_message(
                    chat_id=user.telegram_id,
                    from_chat_id=source_message.chat.id,
                    message_id=source_message.message_id,
                )
                result.successful += 1
            except Exception:
                result.failed += 1
        except (TelegramForbiddenError, TelegramAPIError) as exc:
            err_str = str(exc).lower()
            if "blocked" in err_str or "user is deactivated" in err_str:
                user.has_blocked_bot = True
                result.blocked += 1
            else:
                result.failed += 1
        except Exception:
            result.failed += 1

        await asyncio.sleep(rate_limit)

    await session.commit()
    return result
