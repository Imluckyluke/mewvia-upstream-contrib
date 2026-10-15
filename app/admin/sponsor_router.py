"""Sponsor channel admin router.

Manages forced-membership channels:
- list active / inactive channels
- add a channel by @username, t.me link, numeric id, or a forwarded message
- verify the bot is an admin of the channel before accepting it
- toggle active state, delete, re-verify access
"""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.sponsor_link import parse_sponsor_input
from app.core.sponsor_service import (
    clear_membership_cache,
    resolve_channel,
    verify_bot_is_admin,
)
from app.core.units import fa_digits
from app.db.models.sponsor_seen import SponsorSeenMember
from app.db.repo import SponsorRepo
from app.filters import IsAdmin
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.styles import Style
from app.ui.texts import t

log = get_logger(__name__)

router = Router(name="admin_sponsor")


class SponsorAddSG(StatesGroup):
    waiting_for_input = State()


async def ready_registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


async def render_sponsor_panel(session: AsyncSession, registry: EmojiRegistry) -> tuple[str, KB]:
    repo = SponsorRepo(session)
    channels = await repo.all()

    seen_counts_raw = (
        await session.execute(
            select(
                SponsorSeenMember.channel_id,
                func.count(SponsorSeenMember.user_id),
            ).group_by(SponsorSeenMember.channel_id)
        )
    ).all()
    seen_map = {int(cid): int(cnt) for cid, cnt in seen_counts_raw}

    on_icon = registry.text("on")
    off_icon = registry.text("off")
    title_icon = registry.text("sponsor")

    if not channels:
        text = (
            f"{title_icon} <b>مدیریت کانال‌های اسپانسر (عضویت اجباری)</b>\n\n"
            "هنوز هیچ کانال اسپانسری ثبت نشده است.\n"
            "با افزودن کانال، کاربران پیش از استفاده از ربات ملزم به عضویت در آن می‌شوند.\n\n"
            "<i>نکته: ربات باید در کانال ادمین باشد تا بتواند عضویت را بررسی کند.</i>"
        )
    else:
        lines = [f"{title_icon} <b>لیست کانال‌های اسپانسر و آمار ممبرها</b>\n"]
        for c in channels:
            status = f"{on_icon} فعال" if c.is_active else f"{off_icon} غیرفعال"
            link_info = f"@{c.username}" if c.username else f"<code>{c.channel_id}</code>"
            seen_cnt = seen_map.get(c.channel_id, 0)
            lines.append(
                f"• <b>{c.title}</b> — {link_info} — {status} "
                f"(👥 <code>{fa_digits(str(seen_cnt))}</code> تاییدشده)"
            )
        text = "\n".join(lines)

    kb = KB(registry)
    for c in channels:
        kb.row(
            inline_button(
                c.title[:24],
                callback_data=f"sponsor_act:info:{c.id}",
                style=Style.NEUTRAL,
                emoji_slot="sponsor",
                registry=registry,
            ),
            inline_button(
                "فعال" if c.is_active else "غیرفعال",
                callback_data=f"sponsor_act:toggle:{c.id}",
                style=Style.CONFIRM if c.is_active else Style.NEUTRAL,
                emoji_slot="on" if c.is_active else "off",
                registry=registry,
            ),
            inline_button(
                "حذف",
                callback_data=f"sponsor_act:delete:{c.id}",
                style=Style.DESTRUCTIVE,
                emoji_slot="delete",
                registry=registry,
            ),
        )

    kb.row(
        inline_button(
            "افزودن کانال اسپانسر",
            callback_data="sponsor_act:add",
            style=Style.CONFIRM,
            emoji_slot="add",
            registry=registry,
        )
    )
    kb.row(
        inline_button(
            t("common.refresh"),
            callback_data="sponsor_act:list",
            style=Style.PRIMARY,
            emoji_slot="refresh",
            registry=registry,
        ),
        inline_button(
            t("common.back"),
            callback_data="menu:admin",
            style=Style.PRIMARY,
            emoji_slot="back",
            registry=registry,
        ),
    )
    return text, kb


async def _show_panel(query: CallbackQuery, session: AsyncSession) -> None:
    registry = await ready_registry()
    text, kb = await render_sponsor_panel(session, registry)
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text(text, reply_markup=kb.as_markup())
        except Exception:
            await query.message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "admin_act:sponsors", IsAdmin())
@router.callback_query(F.data == "sponsor_act:list", IsAdmin())
async def list_sponsors(
    query: CallbackQuery, session: AsyncSession, state: FSMContext | None = None
) -> None:
    if state is not None:
        await state.clear()
    await _show_panel(query, session)
    await query.answer()


@router.callback_query(F.data.startswith("sponsor_act:toggle:"), IsAdmin())
async def toggle_sponsor(query: CallbackQuery, session: AsyncSession) -> None:
    channel_db_id = int((query.data or "").split(":")[-1])
    repo = SponsorRepo(session)
    channel = await repo.get_by_id(channel_db_id)
    if channel is None:
        await query.answer("کانال یافت نشد.", show_alert=True)
        return

    await repo.set_active(channel_db_id, not channel.is_active)
    await session.flush()
    clear_membership_cache()
    await query.answer("وضعیت کانال تغییر کرد.")
    await _show_panel(query, session)


@router.callback_query(F.data.startswith("sponsor_act:delete:"), IsAdmin())
async def delete_sponsor(query: CallbackQuery, session: AsyncSession) -> None:
    channel_db_id = int((query.data or "").split(":")[-1])
    repo = SponsorRepo(session)
    deleted = await repo.delete(channel_db_id)
    await session.flush()
    clear_membership_cache()
    await query.answer("کانال حذف شد." if deleted else "کانال یافت نشد.", show_alert=True)
    await _show_panel(query, session)


@router.callback_query(F.data.startswith("sponsor_act:info:"), IsAdmin())
async def info_sponsor(query: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    channel_db_id = int((query.data or "").split(":")[-1])
    repo = SponsorRepo(session)
    channel = await repo.get_by_id(channel_db_id)
    if channel is None:
        await query.answer("کانال یافت نشد.", show_alert=True)
        return

    seen_cnt = (
        await session.execute(
            select(func.count(SponsorSeenMember.user_id)).where(
                SponsorSeenMember.channel_id == channel.channel_id
            )
        )
    ).scalar_one()

    ok, reason = await verify_bot_is_admin(bot, channel.channel_id)
    admin_line = "ربات ادمین است ✔" if ok else f"ربات ادمین نیست ({reason[:40]})"

    await query.answer(
        f"{channel.title}\n\n"
        f"آیدی: {channel.channel_id}\n"
        f"یوزرنیم: {('@' + channel.username) if channel.username else 'ندارد'}\n"
        f"لینک: {channel.invite_link or 'ندارد'}\n"
        f"وضعیت: {'فعال' if channel.is_active else 'غیرفعال'}\n"
        f"ممبرهای تاییدشده: {fa_digits(str(seen_cnt))} کاربر\n"
        f"{admin_line}",
        show_alert=True,
    )


@router.callback_query(F.data == "sponsor_act:add", IsAdmin())
async def start_add_sponsor(query: CallbackQuery, state: FSMContext) -> None:
    registry = await ready_registry()
    await state.set_state(SponsorAddSG.waiting_for_input)

    kb = KB(registry)
    kb.row(
        inline_button(
            t("common.cancel"),
            callback_data="sponsor_act:cancel_add",
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        )
    )

    text = (
        f"{registry.text('add')} <b>افزودن کانال اسپانسر</b>\n\n"
        "یکی از این کارها را انجام بده:\n"
        f"{registry.text('bullet')} یک پیام از کانال را به اینجا <b>فوروارد</b> کن"
        " (برای کانال خصوصی).\n"
        f"{registry.text('bullet')} یا یوزرنیم / لینک / آیدی عددی کانال را بفرست:\n"
        "<code>@mychannel</code> — <code>https://t.me/mychannel</code> — "
        "<code>-1001234567890</code>\n\n"
        f"{registry.text('warning')} <b>مهم:</b> ربات باید در آن کانال <b>ادمین</b> باشد، "
        "وگرنه امکان بررسی عضویت کاربران وجود ندارد."
    )
    if isinstance(query.message, Message):
        await query.message.edit_text(text, reply_markup=kb.as_markup())
    await query.answer()


@router.callback_query(F.data == "sponsor_act:cancel_add", IsAdmin())
async def cancel_add_sponsor(
    query: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    await state.clear()
    await _show_panel(query, session)
    await query.answer(t("common.cancel"))


@router.message(SponsorAddSG.waiting_for_input, IsAdmin())
async def process_sponsor_input(
    message: Message, session: AsyncSession, state: FSMContext, bot: Bot
) -> None:
    registry = await ready_registry()

    # 1. Forwarded message from a channel — the only way to get a private channel's id.
    target: str | int | None = None
    forwarded = getattr(message, "forward_from_chat", None)
    if forwarded is None:
        origin = getattr(message, "forward_origin", None)
        origin_chat = getattr(origin, "chat", None) if origin else None
        if origin_chat is not None:
            forwarded = origin_chat

    if forwarded is not None:
        target = forwarded.id
    elif message.text:
        raw = message.text.strip()
        username_or_hash, chat_id = parse_sponsor_input(raw)
        if chat_id is not None:
            target = chat_id
        elif username_or_hash and not username_or_hash.startswith("+"):
            target = f"@{username_or_hash}"
        elif username_or_hash and username_or_hash.startswith("+"):
            await message.reply(
                f"{registry.text('lock')} این لینک خصوصی است و تلگرام اجازه نمی‌دهد آیدی کانال "
                "از روی آن خوانده شود.\nلطفاً یک پیام از آن کانال را به اینجا <b>فوروارد</b> کن."
            )
            return
        else:
            target = raw
    else:
        await message.reply(
            f"{registry.text('error')} لطفاً یوزرنیم، لینک، آیدی عددی یا یک پیام فوروارد شده بفرست."
        )
        return

    resolved = await resolve_channel(bot, target)
    if resolved is None:
        await message.reply(
            f"{registry.text('error')} کانال شناسایی نشد یا آیدی نامعتبر است.\n"
            "مطمئن شو ربات در کانال <b>ادمین</b> است و سپس دوباره تلاش کن "
            "(یا یک پیام از کانال را فوروارد کن)."
        )
        return

    channel_id, title, username, invite_link = resolved

    is_admin, reason = await verify_bot_is_admin(bot, channel_id)
    if not is_admin:
        await message.reply(
            f"{registry.text('warning')} ربات در کانال <b>{title}</b> ادمین نیست.\n\n"
            "ابتدا ربات را در آن کانال ادمین کن تا بتواند عضویت کاربران را بررسی کند، "
            "سپس دوباره همین کانال را بفرست.\n"
            f"<i>جزئیات: {reason[:80]}</i>"
        )
        return

    repo = SponsorRepo(session)
    channel = await repo.add_or_update(
        channel_id=channel_id,
        title=title,
        username=username,
        invite_link=invite_link,
        is_active=True,
    )
    await session.flush()
    clear_membership_cache()
    await state.clear()

    text, kb = await render_sponsor_panel(session, registry)
    await message.reply(
        f"{registry.text('ok')} کانال اسپانسر <b>{channel.title}</b> ثبت شد.\n\n{text}",
        reply_markup=kb.as_markup(),
    )


# Backward-compat alias: the old two-step add flow exposed this name and
# older tests still import it. The flow is now single-step via
# process_sponsor_input, which also accepts the stored FSM payload through
# `state` data for programmatic callers.
async def process_sponsor_title(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("sponsor_channel_id") is not None and message.text:
        repo = SponsorRepo(session)
        channel_id = int(data["sponsor_channel_id"])
        channel = await repo.add_or_update(
            channel_id=channel_id,
            title=message.text.strip(),
            username=data.get("sponsor_username"),
            invite_link=data.get("sponsor_invite_link"),
            is_active=True,
        )
        await session.flush()
        await state.clear()
        registry = await ready_registry()
        text, kb = await render_sponsor_panel(session, registry)
        await message.reply(
            f"{registry.text('ok')} کانال اسپانسر <b>{channel.title}</b> ثبت شد.\n\n{text}",
            reply_markup=kb.as_markup(),
        )
        return
    bot_obj = message.bot
    assert isinstance(bot_obj, Bot)
    await process_sponsor_input(message, session, state, bot_obj)
