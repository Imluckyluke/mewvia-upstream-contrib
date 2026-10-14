"""SponsorGateMiddleware — Gatekeeper for forced channel membership."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, html
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logging import get_logger
from app.core.sponsor_service import claim_recent_leaves, get_missing_sponsors
from app.ui.emoji import get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.styles import Style

log = get_logger(__name__)

#: Telegram's pseudo-user for admins posting anonymously (as the group/channel).
#: Membership of this id can never be verified — get_chat_member errors out and
#: the old fail-open path let anonymous /media through unchecked.
ANONYMOUS_SENDER_ID = 1087968824


def build_sponsor_join_keyboard(sponsors: list[Any], registry: Any) -> KB:
    kb = KB(registry)

    for sponsor in sponsors:
        join_url = sponsor.invite_link or (
            f"https://t.me/{sponsor.username}" if sponsor.username else None
        )
        if join_url is None:
            # No public path to this channel exists (private, link rotated,
            # never set). A button to https://t.me lands on Telegram's
            # homepage and reads as a bug — skip the join button and keep
            # only "I joined" so the user asks the admin for a fresh link.
            log.warning(
                "sponsor_join_url_missing",
                channel_id=getattr(sponsor, "channel_id", None),
                title=getattr(sponsor, "title", None),
            )
            continue
        kb.row(
            inline_button(
                sponsor.title,
                url=join_url,
                style=Style.PRIMARY,
                emoji_slot="broadcast",
                registry=registry,
            )
        )

    # Check / Join confirmation button
    kb.row(
        inline_button(
            "عضو شدم",
            callback_data="check_sponsors",
            style=Style.CONFIRM,
            emoji_slot="ok",
            registry=registry,
        )
    )

    return kb


class SponsorGateMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        session: AsyncSession | None = data.get("session")
        bot: Bot | None = data.get("bot")

        if not user or not session or not bot:
            return await handler(event, data)

        # 1. Admin Exception: Admins bypass sponsor gate completely
        if get_settings().is_admin(user.id):
            return await handler(event, data)

        # 0. Membership bookkeeping (chat_member updates) feeds the instant
        # leave-watcher — the gate only guards user-initiated actions.
        inner_for_anon = getattr(event, "event", event)
        if isinstance(inner_for_anon, ChatMemberUpdated):
            return await handler(event, data)

        # 1b. Anonymous senders (admins posting "as group/channel"): Telegram
        # marks these with the GroupAnonymousBot id and/or a sender_chat, and
        # membership can never be verified — get_chat_member errors out and
        # the old fail-open path let anonymous /media through unchecked.
        # Deny with guidance instead. (Narrow: only the anonymous pseudo-user
        # and sender_chat posts, so no other bot's behavior changes.)
        sender_chat = getattr(inner_for_anon, "sender_chat", None)
        if user.id == ANONYMOUS_SENDER_ID or sender_chat is not None:
            deny_chat_id = getattr(getattr(inner_for_anon, "chat", None), "id", None)
            log.info(
                "sponsor_gate_anonymous_denied",
                user_id=user.id,
                chat_id=deny_chat_id,
            )
            if isinstance(inner_for_anon, CallbackQuery):
                await inner_for_anon.answer(
                    "حالت ناشناس روشن است — اول آن را خاموش کنید.", show_alert=True
                )
            elif isinstance(inner_for_anon, Message):
                await inner_for_anon.answer(
                    "حالت «ارسال ناشناس» روشن است — عضویت شما قابل بررسی نیست.\n"
                    "لطفاً حالت ارسال به‌نام گروه را خاموش کنید "
                    "و با اکانت خودتان دوباره تلاش کنید."
                )
            return None

        inner = inner_for_anon

        # 2. Group / Supergroup non-command message bypass:
        # Do not check sponsors or prompt in group chats for regular chat
        # messages, stickers, photos, etc.
        # BUT if it is a command OR contains a supported media URL, enforce the sponsor gate!
        if isinstance(inner, Message) and inner.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            text = (inner.text or inner.caption or "").strip()
            is_cmd = text.startswith("/")
            has_media_url = False
            if not is_cmd and text:
                from app.media.downloader import detect_platform, extract_first_url

                found_url = extract_first_url(text)
                if found_url:
                    key, _, _ = detect_platform(found_url)
                    if key != "other" or (
                        found_url.startswith("http://") or found_url.startswith("https://")
                    ):
                        has_media_url = True

            if not is_cmd and not has_media_url:
                return await handler(event, data)

        # 3. Handler Exception: Let "check_sponsors" callback through to its handler
        if isinstance(inner, CallbackQuery) and inner.data == "check_sponsors":
            return await handler(event, data)

        # 3. Check for missing sponsor channels
        missing = await get_missing_sponsors(bot, session, user.id)
        update_kind = type(inner).__name__
        chat_id = getattr(getattr(inner, "chat", None), "id", None)
        if not missing:
            log.info(
                "sponsor_gate_passed",
                user_id=user.id,
                chat_id=chat_id,
                update_kind=update_kind,
            )
            return await handler(event, data)

        # 4. User has missing channels -> block handler execution and show prompt
        log.info(
            "sponsor_gate_blocked",
            user_id=user.id,
            chat_id=chat_id,
            update_kind=update_kind,
            missing=[c.channel_id for c in missing],
        )
        registry = get_emoji_registry()
        await registry.refresh_premium_flag()
        await registry.load()

        kb = build_sponsor_join_keyboard(missing, registry)

        # Warm re-invite for leavers: a fresh False for a previously-seen
        # member means they just left mid-session. One friendly nudge, then
        # the standard gate prompt as usual.
        left_now = claim_recent_leaves(user.id)
        is_group = isinstance(inner, Message) and inner.chat.type in (
            ChatType.GROUP,
            ChatType.SUPERGROUP,
        )
        if left_now and not is_group:
            first = left_now[0]
            first_name = html.quote(
                user.first_name if getattr(user, "first_name", None) else "دوست من"
            )
            title = html.quote(first.title)
            left_text = (
                f"{registry.text('celebrate')} {first_name} عزیز، دیدم از کانال "
                f"<b>{title}</b> خارج شدی!\n\n"
                "خیلی دلمون برات تنگ میشه — ولی برای اینکه بتونی از امکانات ربات "
                "استفاده کنی، باید دوباره عضوش بشی.\n"
                "یه کلیک فاصله داری، منتظرتم!"
            )
            try:
                await bot.send_message(
                    chat_id=user.id,
                    text=left_text,
                    reply_markup=kb.as_markup(),
                )
                log.info(
                    "sponsor_leave_invite_sent",
                    user_id=user.id,
                    channel_id=first.channel_id,
                )
            except Exception as exc:
                log.warning("sponsor_leave_invite_failed", error=str(exc))

        is_group = isinstance(inner, Message) and inner.chat.type in (
            ChatType.GROUP,
            ChatType.SUPERGROUP,
        )
        first_name = html.quote(user.first_name or "دوست من")
        if is_group:
            prompt_text = (
                f"{registry.text('lock')} <b>عضویت در کانال‌های اسپانسر</b>\n\n"
                f"کاربر {first_name} عزیز، برای استفاده از امکانات ربات در گروه، "
                "لطفاً ابتدا در کانال‌های زیر عضو شوید و سپس دکمه «عضو شدم» را بزنید:"
            )
        else:
            prompt_text = (
                f"{registry.text('lock')} <b>عضویت اجباری در کانال‌های اسپانسر</b>\n\n"
                "برای استفاده از امکانات ربات، لطفاً ابتدا در کانال‌های زیر عضو شوید "
                "و سپس دکمه «عضو شدم» را بزنید:"
            )

        if isinstance(inner, CallbackQuery):
            await inner.answer("لطفاً ابتدا در کانال‌های اسپانسر عضو شوید.", show_alert=True)
            if inner.message and isinstance(inner.message, Message):
                await inner.message.answer(prompt_text, reply_markup=kb.as_markup())
        elif isinstance(inner, Message):
            if is_group:
                await inner.reply(prompt_text, reply_markup=kb.as_markup())
            else:
                await inner.answer(prompt_text, reply_markup=kb.as_markup())

        return None
