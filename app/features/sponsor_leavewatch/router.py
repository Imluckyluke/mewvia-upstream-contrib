"""Instant leave-event outreach (sponsor_leavewatch).

Listens for Telegram chat_member updates on our sponsor channels: the moment
someone's status becomes LEFT/KICKED, send the warm re-invite straight to
their DM — no need to wait for their next bot interaction.

Why a separate feature + DB tracking (not only the gate cache):
* the gate only runs when the user *acts* — a leaver who stays silent never
  triggers it, so the "warm invite on next check" fires too late or never;
* seen-membership rows in sponsor_seen_members survive restarts, so only
  genuinely observed members get the DM (no cold spam to never-joined users).
"""

from __future__ import annotations

from aiogram import Bot, Router, html
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberUpdated
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.logging import get_logger
from app.db.models.sponsor_seen import SponsorSeenMember
from app.db.repo.sponsor import SponsorRepo
from app.features.registry import Feature, register
from app.middlewares.sponsor_gate import build_sponsor_join_keyboard
from app.ui.emoji import EmojiRegistry, get_emoji_registry

log = get_logger(__name__)

router = Router(name="sponsor_leavewatch")


async def ready_registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


@router.chat_member()
async def on_sponsor_member_update(
    event: ChatMemberUpdated,
    bot: Bot,
    session: AsyncSession,
    settings: Settings | None = None,
) -> None:
    """DM the warm re-invite the instant a known member leaves a sponsor channel."""
    old = event.old_chat_member.status if event.old_chat_member else None
    new = event.new_chat_member.status if event.new_chat_member else None
    user = event.from_user
    chat_id = event.chat.id

    channels = await SponsorRepo(session).all()
    if not any(c.channel_id == chat_id and c.is_active for c in channels):
        return

    if new not in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        return

    was_member = old in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    )
    if not was_member:
        # Never an engaged member (join-request declined etc.) — stay silent.
        return

    # One-shot: delete the seen row so repeats/rejoins don't double-DM.
    seen = await session.get(SponsorSeenMember, {"channel_id": chat_id, "user_id": user.id})
    if seen is not None:
        await session.delete(seen)
        await session.flush()

    channel = next(c for c in channels if c.channel_id == chat_id)
    registry = await ready_registry()
    kb = build_sponsor_join_keyboard([channel], registry)
    first_name = html.quote(user.first_name or "دوست من")
    title = html.quote(channel.title)
    text = (
        f"{registry.text('celebrate')} {first_name} عزیز، دیدم از کانال "
        f"<b>{title}</b> خارج شدی!\n\n"
        "خیلی دلمون برات تنگ میشه — ولی برای اینکه بتونی از امکانات ربات "
        "استفاده کنی، باید دوباره عضوش بشی.\n"
        "یه کلیک فاصله داری، منتظرتم!"
    )
    try:
        await bot.send_message(chat_id=user.id, text=text, reply_markup=kb.as_markup())
    except Exception as exc:
        log.warning("sponsor_leave_dm_failed", user_id=user.id, error=str(exc))
        return
    log.info("sponsor_leave_dm_sent", user_id=user.id, channel_id=chat_id)


feature = register(
    Feature(
        name="sponsor_leavewatch",
        router=router,
        order=14,
    )
)
