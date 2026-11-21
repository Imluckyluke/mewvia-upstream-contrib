"""Admin panel feature router.

Complete admin dashboard:
- Bot Statistics (users, conversions, traffic, success rate)
- Broadcast Messaging (copy message to all users, handle blocks)
- User Management & Export (view user list, export JSON / TXT)
- Database Backup (pg_dump -Fc document export)
- Emoji Management (shortcut to live emoji editor / clear all)
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from aiogram import Bot, F, Router, html
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.autobackup import describe_schedule, sync_autobackup_job
from app.admin.backup import create_db_backup
from app.admin.broadcast import send_broadcast
from app.admin.cookies_router import router as cookies_router
from app.admin.loading_gifs_router import router as loading_gifs_router
from app.admin.sponsor_router import router as sponsor_router
from app.admin.stats import (
    AdminStats,
    format_stats_downloader,
    format_stats_media,
    format_stats_overview,
    format_stats_sponsors,
    get_admin_stats,
)
from app.admin.users_export import export_users_json, export_users_txt
from app.callbacks import MenuCB, NoopCB
from app.config import Settings
from app.core.logging import get_logger
from app.core.settings_store import get_settings_store
from app.core.units import fa_digits, human_bytes
from app.db.models.conversion import Conversion
from app.db.models.sponsor_channel import SponsorChannel
from app.db.models.sponsor_seen import SponsorSeenMember
from app.db.models.sticker_set import StickerSet
from app.db.repo import EmojiRepo, UserRepo
from app.features.registry import Feature, register
from app.filters import IsAdmin
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.styles import Style
from app.ui.texts import t

log = get_logger(__name__)

router = Router(name="admin")
_stats_snapshot: tuple[float, AdminStats] | None = None
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
router.include_router(sponsor_router)
router.include_router(cookies_router)
router.include_router(loading_gifs_router)


class BroadcastSG(StatesGroup):
    waiting_for_message = State()
    confirm_broadcast = State()


class UserSearchSG(StatesGroup):
    waiting_for_query = State()


async def ready_registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


async def build_admin_panel_kb(registry: EmojiRegistry) -> KB:
    kb = KB(registry)
    kb.row(
        inline_button(
            "آمار ربات",
            callback_data="admin_act:stats",
            style=Style.PRIMARY,
            emoji_slot="stats",
            registry=registry,
        ),
        inline_button(
            "پیام همگانی",
            callback_data="admin_act:broadcast",
            style=Style.PRIMARY,
            emoji_slot="broadcast",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "لیست کاربران",
            callback_data="admin_act:users",
            style=Style.PRIMARY,
            emoji_slot="users",
            registry=registry,
        ),
        inline_button(
            "کانال‌های اسپانسر",
            callback_data="admin_act:sponsors",
            style=Style.PRIMARY,
            emoji_slot="sponsor",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "مدیریت ایموجی‌ها",
            callback_data="menu:uidemo",
            style=Style.CONFIRM,
            emoji_slot="emoji",
            registry=registry,
        ),
        inline_button(
            "دریافت بکاپ",
            callback_data="admin_act:backup",
            style=Style.CONFIRM,
            emoji_slot="backup",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "کوکی‌های اینستاگرام",
            callback_data="admin_act:cookies",
            style=Style.PRIMARY,
            emoji_slot="tool",
            registry=registry,
        ),
        inline_button(
            "گیف‌های لودینگ",
            callback_data="admin_act:loading_gifs",
            style=Style.PRIMARY,
            emoji_slot="video",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "تنظیمات ربات",
            callback_data="admin_act:settings",
            style=Style.PRIMARY,
            emoji_slot="admin",
            registry=registry,
        ),
        inline_button(
            "پاکسازی تمام ایموجی‌ها",
            callback_data="admin_act:clear_emojis",
            style=Style.DESTRUCTIVE,
            emoji_slot="delete",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            t("common.back"),
            callback_data="menu:root",
            style=Style.PRIMARY,
            emoji_slot="back",
            registry=registry,
        )
    )
    return kb


@router.message(F.text.regexp(r"^/admin(@\w+)?$"), IsAdmin())
async def cmd_admin(message: Message, session: AsyncSession) -> None:
    registry = await ready_registry()
    kb = await build_admin_panel_kb(registry)
    await message.answer(
        f"<b>{registry.text('admin')} پنل مدیریت ربات Mewvia</b>", reply_markup=kb.as_markup()
    )


@router.callback_query(MenuCB.filter(F.target == "admin"), IsAdmin())
async def menu_admin(query: CallbackQuery, session: AsyncSession) -> None:
    registry = await ready_registry()
    kb = await build_admin_panel_kb(registry)
    if isinstance(query.message, Message):
        await query.message.edit_text(
            f"<b>{registry.text('admin')} پنل مدیریت ربات Mewvia</b>",
            reply_markup=kb.as_markup(),
        )
    await query.answer()


# ---------------------------------------------------------------------------
# Statistics Action & Multi-Tab Dashboard
# ---------------------------------------------------------------------------


def _build_stats_tabs_kb(current_tab: str, registry: EmojiRegistry) -> KB:
    kb = KB(registry)
    # Row 1: Interactive tab navigation
    kb.row(
        inline_button(
            "کاربران" + (" ✅" if current_tab == "overview" else ""),
            callback_data="admin_stats:overview",
            style=Style.CONFIRM if current_tab == "overview" else Style.PRIMARY,
            emoji_slot="users",
            registry=registry,
        ),
        inline_button(
            "مدیا" + (" ✅" if current_tab == "media" else ""),
            callback_data="admin_stats:media",
            style=Style.CONFIRM if current_tab == "media" else Style.PRIMARY,
            emoji_slot="convert",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "دانلودر" + (" ✅" if current_tab == "downloader" else ""),
            callback_data="admin_stats:downloader",
            style=Style.CONFIRM if current_tab == "downloader" else Style.PRIMARY,
            emoji_slot="download",
            registry=registry,
        ),
        inline_button(
            "اسپانسر" + (" ✅" if current_tab == "sponsors" else ""),
            callback_data="admin_stats:sponsors",
            style=Style.CONFIRM if current_tab == "sponsors" else Style.PRIMARY,
            emoji_slot="sponsor",
            registry=registry,
        ),
    )
    # Row 3: Refresh & Back
    refresh_cb = "admin_act:stats" if current_tab == "overview" else f"admin_stats:{current_tab}"
    kb.row(
        inline_button(
            t("common.refresh"),
            callback_data=refresh_cb,
            style=Style.CONFIRM,
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
    return kb


@router.callback_query(
    F.data.in_(
        {
            "admin_act:stats",
            "admin_stats:overview",
            "admin_stats:media",
            "admin_stats:downloader",
            "admin_stats:sponsors",
        }
    ),
    IsAdmin(),
)
async def admin_stats_action(query: CallbackQuery, session: AsyncSession) -> None:
    if not isinstance(query.message, Message):
        await query.answer()
        return

    tab = "overview"
    raw_data = query.data or ""
    if raw_data.startswith("admin_stats:"):
        tab = raw_data.split(":", 1)[1]

    global _stats_snapshot
    if _stats_snapshot is None or time.monotonic() >= _stats_snapshot[0]:
        _stats_snapshot = (time.monotonic() + 10, await get_admin_stats(session))
    stats = _stats_snapshot[1]
    registry = await ready_registry()

    if tab == "media":
        text = format_stats_media(stats, registry)
    elif tab == "downloader":
        text = format_stats_downloader(stats, registry)
    elif tab == "sponsors":
        text = format_stats_sponsors(stats, registry)
    else:
        text = format_stats_overview(stats, registry)
        tab = "overview"

    kb = _build_stats_tabs_kb(tab, registry)
    try:
        await query.message.edit_text(text, reply_markup=kb.as_markup())
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            log.warning("admin_stats_edit_failed", error=str(exc))
    except Exception as exc:
        log.warning("admin_stats_error", error=str(exc))
    await query.answer()


# ---------------------------------------------------------------------------
# User List & Export Action
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# User List, Pagination, Search & Profile Inspection
# ---------------------------------------------------------------------------

USER_LIST_PAGE_SIZE = 5


async def _render_users_page(
    session: AsyncSession,
    registry: EmojiRegistry,
    page: int = 1,
    query: str | None = None,
) -> tuple[str, KB]:
    repo = UserRepo(session)
    total = await repo.count(query=query)
    total_pages = max(1, (total + USER_LIST_PAGE_SIZE - 1) // USER_LIST_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * USER_LIST_PAGE_SIZE
    users = await repo.list_page(offset=offset, limit=USER_LIST_PAGE_SIZE, query=query)

    lines = ["<b>👥 مدیریت و لیست کاربران ربات Mewvia</b>\n"]
    if query:
        lines.append(f"🔍 <i>نتایج جستجو برای:</i> <code>{html.quote(query)}</code>")
    lines.append(
        f"• کل نتایج: <code>{fa_digits(str(total))}</code> کاربر | "
        f"صفحه: <code>{fa_digits(str(page))}</code> از <code>{fa_digits(str(total_pages))}</code>\n"
    )

    if not users:
        lines.append("<i>هیچ کاربری با این مشخصات یافت نشد.</i>\n")
    else:
        for idx, u in enumerate(users, start=offset + 1):
            uname = f"@{u.username}" if u.username else "بدون یوزرنیم"
            fname = html.quote(u.first_name or "کاربر")
            prem = " ⭐️" if u.is_premium else ""
            banned = " 🚫" if u.is_banned else ""
            header = (
                f"{fa_digits(str(idx))}. <b>{fname}</b> ({uname}) — "
                f"<code>{u.telegram_id}</code>{prem}{banned}\n"
                f"   🗓 <code>{u.created_at.strftime('%Y-%m-%d')}</code> | "
                f"تعاملات: <code>{fa_digits(str(u.updates_seen))}</code>"
            )
            lines.append(header)
        lines.append("")

    lines.append("💡 <i>برای مشاهده جزئیات یا مدیریت هر کاربر، روی دکمه آن بزنید:</i>")

    kb = KB(registry)

    # User Selection Buttons
    for u in users:
        label_name = (u.first_name or u.username or str(u.telegram_id))[:18]
        prem_mark = " ⭐️" if u.is_premium else ""
        ban_mark = " 🚫" if u.is_banned else ""
        btn_label = f"👤 {label_name}{prem_mark}{ban_mark}"
        kb.row(
            inline_button(
                btn_label,
                callback_data=f"admin_user:view:{u.telegram_id}:{page}",
                style=Style.PRIMARY if not u.is_banned else Style.DESTRUCTIVE,
                emoji_slot="user",
                registry=registry,
            )
        )

    # Navigation Row (Previous, Page Indicator, Next)
    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            inline_button(
                "قبلی",
                callback_data=f"admin_users:page:{page - 1}",
                style=Style.PRIMARY,
                emoji_slot="back",
                registry=registry,
            )
        )
    nav_buttons.append(
        inline_button(
            f"{fa_digits(str(page))} / {fa_digits(str(total_pages))}",
            callback_data=NoopCB().pack(),
            style=Style.CONFIRM,
            emoji_slot="bullet",
            registry=registry,
        )
    )
    if page < total_pages:
        nav_buttons.append(
            inline_button(
                "بعدی",
                callback_data=f"admin_users:page:{page + 1}",
                style=Style.PRIMARY,
                emoji_slot="next",
                registry=registry,
            )
        )
    kb.row(*nav_buttons)

    # Search & Tool Buttons
    tool_buttons = [
        inline_button(
            "جستجوی کاربر",
            callback_data="admin_users:search",
            style=Style.PRIMARY,
            emoji_slot="search",
            registry=registry,
        )
    ]
    if query:
        tool_buttons.append(
            inline_button(
                "لغو جستجو",
                callback_data="admin_act:users",
                style=Style.DESTRUCTIVE,
                emoji_slot="delete",
                registry=registry,
            )
        )
    tool_buttons.append(
        inline_button(
            t("common.refresh"),
            callback_data=f"admin_users:page:{page}",
            style=Style.CONFIRM,
            emoji_slot="refresh",
            registry=registry,
        )
    )
    kb.row(*tool_buttons)

    # Exports Row
    kb.row(
        inline_button(
            "خروجی JSON",
            callback_data="admin_act:export_json",
            style=Style.PRIMARY,
            emoji_slot="file",
            registry=registry,
        ),
        inline_button(
            "خروجی TXT",
            callback_data="admin_act:export_txt",
            style=Style.PRIMARY,
            emoji_slot="file",
            registry=registry,
        ),
    )

    # Back Row
    kb.row(
        inline_button(
            t("common.back"),
            callback_data="menu:admin",
            style=Style.PRIMARY,
            emoji_slot="back",
            registry=registry,
        )
    )

    return "\n".join(lines), kb


async def _render_user_detail(
    user_id: int,
    page: int,
    session: AsyncSession,
    registry: EmojiRegistry,
) -> tuple[str, KB]:
    repo = UserRepo(session)
    user = await repo.get(user_id)
    if user is None:
        kb = KB(registry)
        kb.row(
            inline_button(
                "بازگشت به لیست",
                callback_data=f"admin_users:page:{page}",
                style=Style.PRIMARY,
                emoji_slot="back",
                registry=registry,
            )
        )
        return f"{registry.text('error')} <i>کاربر مورد نظر یافت نشد.</i>", kb

    # Conversion history
    conv_stmt = select(
        func.count(Conversion.id),
        func.coalesce(func.sum(case((Conversion.status == "success", 1), else_=0)), 0),
        func.coalesce(func.sum(Conversion.in_bytes), 0),
        func.coalesce(func.sum(Conversion.out_bytes), 0),
    ).where(Conversion.telegram_id == user_id)
    conv_res = (await session.execute(conv_stmt)).one()
    total_conv, succ_conv, in_b, out_b = (
        int(conv_res[0]),
        int(conv_res[1]),
        int(conv_res[2]),
        int(conv_res[3]),
    )

    # Sticker packs
    stick_stmt = select(
        func.count(StickerSet.id),
        func.coalesce(func.sum(StickerSet.stickers_count), 0),
    ).where(StickerSet.telegram_id == user_id)
    stick_res = (await session.execute(stick_stmt)).one()
    packs_cnt, stickers_cnt = int(stick_res[0]), int(stick_res[1])

    # Sponsor seen channels
    sp_stmt = (
        select(SponsorChannel.title)
        .join(
            SponsorSeenMember,
            SponsorChannel.channel_id == SponsorSeenMember.channel_id,
        )
        .where(SponsorSeenMember.user_id == user_id)
    )
    sp_titles = (await session.execute(sp_stmt)).scalars().all()

    uname = f"@{user.username}" if user.username else "ندارد"
    fullname = html.quote(f"{user.first_name or ''} {user.last_name or ''}".strip() or "نامشخص")
    prem_text = "دارد ⭐️" if user.is_premium else "ندارد"
    banned_text = (
        f"مسدود (Ban) {registry.text('ban')}"
        if user.is_banned
        else f"مجاز و فعال {registry.text('ok')}"
    )
    block_text = (
        f"ربات را بلاک کرده {registry.text('warning')}"
        if user.has_blocked_bot
        else f"طبیعی {registry.text('ok')}"
    )

    created_str = user.created_at.strftime("%Y-%m-%d %H:%M")
    seen_str = user.last_seen_at.strftime("%Y-%m-%d %H:%M")
    lines = [
        f"<b>{registry.text('user')} کارت مشخصات و مدیریت کاربر</b>\n",
        f"{registry.text('bullet')} <b>نام:</b> {fullname}",
        f"{registry.text('bullet')} <b>یوزرنیم:</b> {uname}",
        f"{registry.text('bullet')} <b>شناسه عددی (ID):</b> <code>{user.telegram_id}</code>",
        f"{registry.text('bullet')} <b>زبان تلگرام:</b> <code>{user.language_code or '-'}</code>",
        f"{registry.text('bullet')} <b>پرمیوم تلگرام:</b> {prem_text}",
        f"{registry.text('bullet')} <b>تاریخ عضویت:</b> <code>{created_str}</code>",
        f"{registry.text('bullet')} <b>آخرین فعالیت:</b> <code>{seen_str}</code>",
        f"{registry.text('bullet')} <b>تعداد تعاملات:</b> "
        f"<code>{fa_digits(str(user.updates_seen))}</code> بار",
        f"{registry.text('bullet')} <b>وضعیت بلاک:</b> {block_text}",
        f"{registry.text('bullet')} <b>وضعیت دسترسی:</b> {banned_text}\n",
        f"<b>{registry.text('stats')} سابقه فعالیت‌های مدیا:</b>",
        f"{registry.text('bullet')} کل تبدیل‌ها: <code>{fa_digits(str(total_conv))}</code> "
        f"(موفق: <code>{fa_digits(str(succ_conv))}</code>)",
        f"{registry.text('bullet')} ترافیک کل مصرفی: <code>{human_bytes(in_b + out_b)}</code>",
        f"{registry.text('bullet')} پک‌های استیکر: <code>{fa_digits(str(packs_cnt))}</code> "
        f"(استیکرها: <code>{fa_digits(str(stickers_cnt))}</code>)",
    ]
    if sp_titles:
        lines.append(
            f"{registry.text('bullet')} عضویت در اسپانسر: "
            f"{', '.join(sp_titles)} {registry.text('ok')}"
        )
    else:
        lines.append(f"{registry.text('bullet')} عضویت در اسپانسر: ثبت نشده")

    kb = KB(registry)
    ban_label = "رفع مسدودی (Unban)" if user.is_banned else "مسدود کردن (Ban)"
    ban_style = Style.CONFIRM if user.is_banned else Style.DESTRUCTIVE
    ban_emoji = "unban" if user.is_banned else "ban"
    kb.row(
        inline_button(
            ban_label,
            callback_data=f"admin_user:toggle_ban:{user.telegram_id}:{page}",
            style=ban_style,
            emoji_slot=ban_emoji,
            registry=registry,
        )
    )
    kb.row(
        inline_button(
            "بازگشت به لیست",
            callback_data=f"admin_users:page:{page}",
            style=Style.PRIMARY,
            emoji_slot="back",
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
    return "\n".join(lines), kb


@router.callback_query(F.data == "admin_act:users", IsAdmin())
async def admin_users_action(
    query: CallbackQuery, session: AsyncSession, state: FSMContext | None = None
) -> None:
    if state is not None:
        await state.clear()
    registry = await ready_registry()
    text, kb = await _render_users_page(session, registry, page=1, query=None)
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text(text, reply_markup=kb.as_markup())
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                log.warning("admin_users_edit_failed", error=str(exc))
    await query.answer()


@router.callback_query(F.data.startswith("admin_users:page:"), IsAdmin())
async def admin_users_page(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    page_str = (query.data or "").split(":")[-1]
    page = int(page_str) if page_str.isdigit() else 1
    data = await state.get_data()
    search_query = data.get("search_query")

    registry = await ready_registry()
    text, kb = await _render_users_page(session, registry, page=page, query=search_query)
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text(text, reply_markup=kb.as_markup())
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                log.warning("admin_users_edit_failed", error=str(exc))
    await query.answer()


@router.callback_query(F.data == "admin_users:search", IsAdmin())
async def admin_users_search_start(query: CallbackQuery, state: FSMContext) -> None:
    registry = await ready_registry()
    await state.set_state(UserSearchSG.waiting_for_query)

    kb = KB(registry)
    kb.row(
        inline_button(
            "❌ لغو و انصراف",
            callback_data="admin_users:cancel_search",
            style=Style.DESTRUCTIVE,
            emoji_slot="delete",
            registry=registry,
        )
    )

    text = (
        "🔍 <b>جستجوی کاربر در سیستم</b>\n\n"
        "لطفاً یکی از موارد زیر را ارسال کنید:\n"
        "• <b>آیدی عددی کاربر</b> (مثلاً <code>123456789</code>)\n"
        "• <b>یوزرنیم</b> (با @ یا بدون @)\n"
        "• <b>بخشی از نام یا نام خانوادگی کاربر</b>"
    )
    if isinstance(query.message, Message):
        await query.message.edit_text(text, reply_markup=kb.as_markup())
    await query.answer()


@router.callback_query(F.data == "admin_users:cancel_search", IsAdmin())
async def admin_users_search_cancel(
    query: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    await state.clear()
    registry = await ready_registry()
    text, kb = await _render_users_page(session, registry, page=1, query=None)
    if isinstance(query.message, Message):
        await query.message.edit_text(text, reply_markup=kb.as_markup())
    await query.answer()


@router.message(UserSearchSG.waiting_for_query, IsAdmin())
async def admin_users_search_query(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    raw = (message.text or "").strip()
    query = raw.lstrip("@").strip()
    if not query:
        await message.reply("لطفاً یک متن یا آیدی معتبر برای جستجو بفرستید.")
        return

    await state.update_data(search_query=query)
    registry = await ready_registry()
    text, kb = await _render_users_page(session, registry, page=1, query=query)
    await message.reply(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("admin_user:view:"), IsAdmin())
async def admin_user_view(query: CallbackQuery, session: AsyncSession) -> None:
    parts = (query.data or "").split(":")
    user_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1

    registry = await ready_registry()
    text, kb = await _render_user_detail(user_id, page, session, registry)
    if isinstance(query.message, Message):
        await query.message.edit_text(text, reply_markup=kb.as_markup())
    await query.answer()


@router.callback_query(F.data.startswith("admin_user:toggle_ban:"), IsAdmin())
async def admin_user_toggle_ban(query: CallbackQuery, session: AsyncSession) -> None:
    parts = (query.data or "").split(":")
    user_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1

    repo = UserRepo(session)
    user = await repo.get(user_id)
    if user is None:
        await query.answer("کاربر یافت نشد.", show_alert=True)
        return

    new_state = not user.is_banned
    await repo.set_banned(user_id, new_state)
    await session.commit()

    alert_text = "کاربر مسدود شد (Ban) 🚫" if new_state else "مسدودی کاربر برداشته شد ✅"
    await query.answer(alert_text, show_alert=True)

    registry = await ready_registry()
    text, kb = await _render_user_detail(user_id, page, session, registry)
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text(text, reply_markup=kb.as_markup())
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                log.warning("admin_user_detail_edit_failed", error=str(exc))


@router.callback_query(F.data == "admin_act:export_json", IsAdmin())
async def admin_export_json(
    query: CallbackQuery, session: AsyncSession, settings: Settings, bot: Bot
) -> None:
    await query.answer("در حال ساخت خروجی JSON…")
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir:
        file_path = Path(tmp_dir) / "users.json"
        await export_users_json(session, file_path)
        if isinstance(query.message, Message):
            await bot.send_document(
                query.from_user.id,
                FSInputFile(file_path),
                caption="📄 لیست کامل کاربران (فرمت JSON)",
            )


@router.callback_query(F.data == "admin_act:export_txt", IsAdmin())
async def admin_export_txt(
    query: CallbackQuery, session: AsyncSession, settings: Settings, bot: Bot
) -> None:
    await query.answer("در حال ساخت خروجی TXT…")
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir:
        file_path = Path(tmp_dir) / "users.txt"
        await export_users_txt(session, file_path)
        if isinstance(query.message, Message):
            await bot.send_document(
                query.from_user.id,
                FSInputFile(file_path),
                caption="📝 لیست کامل کاربران (فرمت TXT)",
            )


# ---------------------------------------------------------------------------
# Backup Action
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin_act:backup", IsAdmin())
async def admin_backup_action(query: CallbackQuery, settings: Settings) -> None:
    await query.answer("در حال پشتیبان‌گیری از دیتابیس…")
    try:
        await create_db_backup(settings, settings.backup_dir)
        if isinstance(query.message, Message):
            await query.message.answer("✅ نسخهٔ پشتیبان روی سرور ذخیره شد.")
    except Exception as exc:
        log.error("admin_backup_error", error=str(exc))
        if isinstance(query.message, Message):
            await query.message.answer("❌ پشتیبان‌گیری با خطا مواجه شد.")


# ---------------------------------------------------------------------------
# Bot Settings (maintenance, quota, auto-backup)
# ---------------------------------------------------------------------------


class SettingsEditSG(StatesGroup):
    waiting_for_value = State()


async def render_settings_panel() -> tuple[str, KB]:
    registry = await ready_registry()
    store = get_settings_store()
    maintenance = await store.get_bool("maintenance", False)
    quota = await store.get_int("daily_quota", 0)
    cron = (await store.get("autobackup_cron", "")).strip()

    maint_icon = "🟢 روشن" if maintenance else "🔴 خاموش"
    quota_text = f"{fa_digits(str(quota))} تبدیل/روز" if quota > 0 else "نامحدود ♾️"
    cron_text = cron if cron else "خاموش"

    text = (
        "<b>⚙️ تنظیمات ربات</b>\n\n"
        f"• حالت تعمیر: <b>{maint_icon}</b>\n"
        f"• سهمیه روزانه هر کاربر: <b>{quota_text}</b>\n"
        f"• بکاپ خودکار: <b><code>{cron_text}</code></b>\n"
        "<i>(کرون مثل 0 4 * * * یعنی هر شب ساعت ۴ — خالی = خاموش)</i>"
    )

    kb = KB(registry)
    kb.row(
        inline_button(
            "تغییر حالت تعمیر",
            callback_data="admin_settings:toggle_maintenance",
            style=Style.CONFIRM if not maintenance else Style.DESTRUCTIVE,
            emoji_slot="tool",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "تغییر سهمیه روزانه",
            callback_data="admin_settings:set_quota",
            style=Style.PRIMARY,
            emoji_slot="stats",
            registry=registry,
        ),
        inline_button(
            "زمان‌بندی بکاپ خودکار",
            callback_data="admin_settings:set_cron",
            style=Style.PRIMARY,
            emoji_slot="backup",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "گیف‌های لودینگ نوار پیشرفت",
            callback_data="admin_act:loading_gifs",
            style=Style.PRIMARY,
            emoji_slot="video",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "بکاپ فوری",
            callback_data="admin_act:backup",
            style=Style.CONFIRM,
            emoji_slot="backup",
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


async def _show_settings(query: CallbackQuery) -> None:
    text, kb = await render_settings_panel()
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text(text, reply_markup=kb.as_markup())
        except TelegramBadRequest:
            await query.message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data == "admin_act:settings", IsAdmin())
async def admin_settings_action(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_settings(query)
    await query.answer()


@router.callback_query(F.data == "admin_settings:toggle_maintenance", IsAdmin())
async def admin_toggle_maintenance(query: CallbackQuery) -> None:
    store = get_settings_store()
    new_value = await store.toggle("maintenance")
    log.info("maintenance_toggled", enabled=new_value)
    await query.answer("حالت تعمیر روشن شد 🔧" if new_value else "حالت تعمیر خاموش شد ✅")
    await _show_settings(query)


@router.callback_query(F.data == "admin_settings:set_quota", IsAdmin())
async def admin_quota_prompt(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsEditSG.waiting_for_value)
    await state.update_data(edit_key="daily_quota")
    if isinstance(query.message, Message):
        await query.message.edit_text(
            "<b>📊 سهمیه روزانه</b>\n\n"
            "عدد بفرست (تعداد تبدیل موفق در ۲۴ ساعت برای هر کاربر).\n"
            "<code>0</code> یعنی نامحدود.",
        )
    await query.answer()


@router.callback_query(F.data == "admin_settings:set_cron", IsAdmin())
async def admin_cron_prompt(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsEditSG.waiting_for_value)
    await state.update_data(edit_key="autobackup_cron")
    if isinstance(query.message, Message):
        await query.message.edit_text(
            "<b>💾 بکاپ خودکار</b>\n\n"
            "کرون بفرست (مثلاً <code>0 4 * * *</code> = هر شب ساعت ۴).\n"
            "کلمه <code>خاموش</code> یعنی غیرفعال.\n"
            "فایل‌ها تو پوشه بکاپ سرور نگه داشته میشن (۷ روز)."
        )
    await query.answer()


@router.message(SettingsEditSG.waiting_for_value, IsAdmin())
async def admin_settings_save(
    message: Message, state: FSMContext, bot: Bot, settings: Settings
) -> None:
    data = await state.get_data()
    key = str(data.get("edit_key", ""))
    raw = (message.text or "").strip()
    if not key or not raw:
        await state.clear()
        await message.reply("لغو شد.")
        return

    store = get_settings_store()
    if key == "daily_quota":
        try:
            num = int(raw)
        except ValueError:
            await message.reply("عدد بفرست! (مثلاً 20 یا 0 برای نامحدود)")
            return
        if num < 0:
            await message.reply("عدد منفی نمیشه! 0 یعنی نامحدود.")
            return
        await store.set("daily_quota", str(num))
        log.info("daily_quota_set", quota=num)
    elif key == "autobackup_cron":
        if raw in ("خاموش", "off", "0", "-"):
            await store.set("autobackup_cron", "")
        else:
            check = describe_schedule(raw)
            if check != raw:
                await message.reply(f"کرون نامعتبره! ({check})\nمثال درست: <code>0 4 * * *</code>")
                return
            await store.set("autobackup_cron", raw)
        await sync_autobackup_job(settings, bot)
        log.info("autobackup_cron_set", cron=raw)
    else:
        await state.clear()
        return

    await state.clear()
    text, kb = await render_settings_panel()
    await message.reply(text, reply_markup=kb.as_markup())


# ---------------------------------------------------------------------------
# Clear All Premium Emojis Action
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin_act:clear_emojis", IsAdmin())
async def admin_clear_emojis_action(query: CallbackQuery, session: AsyncSession) -> None:
    registry = await ready_registry()
    repo = EmojiRepo(session)
    admin_id = query.from_user.id if query.from_user else None
    cleared = await repo.clear_all(updated_by=admin_id)
    await registry.invalidate()

    await query.answer(f"تمام ایموجی‌های پریمیوم ({cleared} اسلات) پاکسازی شدند.", show_alert=True)
    kb = await build_admin_panel_kb(registry)
    if isinstance(query.message, Message):
        await query.message.edit_text(
            "<b>🛠 پنل مدیریت ربات Mewvia</b>", reply_markup=kb.as_markup()
        )


# ---------------------------------------------------------------------------
# Broadcast Action
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "admin_act:broadcast", IsAdmin())
async def start_broadcast(query: CallbackQuery, state: FSMContext) -> None:
    registry = await ready_registry()
    await state.set_state(BroadcastSG.waiting_for_message)

    kb = KB(registry)
    kb.row(
        inline_button(
            t("common.cancel"),
            callback_data="admin_act:cancel_broadcast",
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        )
    )

    text = (
        "<b>📣 ارسال پیام همگانی</b>\n\n"
        "لطفاً پیامی که می‌خواهی به تمام کاربران ارسال شود را بفرست.\n"
        "پیام می‌تواند شامل <b>متن، عکس، ویدیو، استیکر یا دکمه شیشه‌ای</b> باشد."
    )
    if isinstance(query.message, Message):
        await query.message.edit_text(text, reply_markup=kb.as_markup())
    await query.answer()


@router.callback_query(F.data == "admin_act:cancel_broadcast", IsAdmin())
async def cancel_broadcast(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await menu_admin(query, session)


@router.message(BroadcastSG.waiting_for_message, IsAdmin())
async def prompt_broadcast_confirm(message: Message, state: FSMContext) -> None:
    registry = await ready_registry()
    await state.update_data(broadcast_msg_id=message.message_id, broadcast_chat_id=message.chat.id)
    await state.set_state(BroadcastSG.confirm_broadcast)

    kb = KB(registry)
    kb.row(
        inline_button(
            "تایید و ارسال همگانی",
            callback_data="admin_act:do_broadcast",
            style=Style.CONFIRM,
            emoji_slot="ok",
            registry=registry,
        ),
        inline_button(
            t("common.cancel"),
            callback_data="admin_act:cancel_broadcast",
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        ),
    )

    await message.reply(
        "<b>پیش‌نمایش پیام همگانی ثبت شد. آیا از ارسال این پیام به تمام کاربران اطمینان داری؟</b>",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data == "admin_act:do_broadcast", BroadcastSG.confirm_broadcast, IsAdmin())
async def execute_broadcast(
    query: CallbackQuery, session: AsyncSession, state: FSMContext, settings: Settings
) -> None:
    data = await state.get_data()
    msg_id = data.get("broadcast_msg_id")
    chat_id = data.get("broadcast_chat_id")
    await state.clear()

    if not isinstance(query.message, Message) or not msg_id or not chat_id:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    bot = query.bot
    if bot is None:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    await query.message.edit_text("<b>⏳ در حال ارسال همگانی پیام… لطفاً شکیبا باشید.</b>")

    source_msg = await bot.forward_message(chat_id=chat_id, from_chat_id=chat_id, message_id=msg_id)

    result = await send_broadcast(bot, session, source_msg)

    try:
        await source_msg.delete()
    except Exception:
        pass

    report = (
        "<b>📣 گزارش ارسال پیام همگانی:</b>\n\n"
        f"• کل هدف‌ها: <code>{fa_digits(str(result.total))}</code>\n"
        f"• موفق: <code>{fa_digits(str(result.successful))}</code>\n"
        f"• ناموفق: <code>{fa_digits(str(result.failed))}</code>\n"
        f"• کاربر ربات را بلاک کرده: <code>{fa_digits(str(result.blocked))}</code>"
    )
    await query.message.answer(report)


feature = register(
    Feature(
        name="admin",
        router=router,
        menu_label_key="menu.admin",
        menu_emoji_slot="admin",
        menu_style=Style.DESTRUCTIVE,
        menu_admin_only=True,
        order=10,
    )
)
