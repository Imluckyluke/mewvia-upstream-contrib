"""Baseline handlers: /start with the real menu, /ping, /me, /help, /health.

The UI showcase lives in `app.features.uidemo`.
"""

from __future__ import annotations

import time

from aiogram import Bot, F, Router, html
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import MenuCB, NoopCB
from app.config import Settings
from app.core.logging import get_logger
from app.core.settings_store import get_settings_store
from app.core.units import human_bytes
from app.db.repo import EmojiRepo
from app.features.registry import Feature, register
from app.filters import IsAdmin
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB
from app.ui.labels import ReplyTextFilter
from app.ui.menu import enabled_menu_features, main_menu, persistent_keyboard
from app.ui.styles import Style
from app.ui.texts import get_help_body, t

router = Router(name="basic")

log = get_logger(__name__)


async def ready_registry() -> EmojiRegistry:
    """Emoji registry with fresh slot data and a current premium flag.

    Called once per screen render rather than per button, so a screen is always
    internally consistent even if an admin toggles the switch mid-render.
    """
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


def back_to_menu(registry: EmojiRegistry) -> KB:
    return KB(registry).button(
        t("common.back"),
        callback_data=MenuCB(target="root").pack(),
        style=Style.PRIMARY,
        emoji_slot="back",
    )


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------


async def send_start(message: Message, is_admin: bool) -> None:
    registry = await ready_registry()
    name = html.quote(message.from_user.first_name if message.from_user else "دوست من")

    body = t("start.greeting", name=name)
    if not await enabled_menu_features(is_admin=is_admin):
        body = f"{body}\n\n<i>{t('menu.empty')}</i>"

    # A message carries either an inline keyboard or a reply keyboard, never both,
    # so the persistent keyboard is attached to its own short message.
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(t("start.pick_action"), reply_markup=persistent_keyboard(registry))

    await message.answer(body, reply_markup=await main_menu(registry, is_admin=is_admin))


@router.message(F.chat.type == ChatType.PRIVATE, CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    is_admin: bool,
    state: FSMContext | None = None,
) -> None:
    if state is not None:
        await state.clear()
    args = (command.args or "").strip()
    if args == "convert":
        await message.answer(t("start.deeplink_convert"))
        return
    if args == "newpack":
        await message.answer(t("start.deeplink_newpack"))
        return
    if args.startswith("addpack_"):
        pack_name = args[8:]
        await message.answer(t("start.deeplink_addpack", pack_name=pack_name))
        return

    await send_start(message, is_admin)


@router.message(F.chat.type == ChatType.PRIVATE, Command("cancel"))
async def cmd_cancel(
    message: Message,
    is_admin: bool,
    state: FSMContext | None = None,
) -> None:
    """Universal state cancel handler."""
    if state is not None:
        await state.clear()
    registry = await ready_registry()
    await message.reply(
        "❌ <b>عملیات لغو شد.</b>\nبه منوی اصلی بازگشتید:",
        reply_markup=await main_menu(registry, is_admin=is_admin),
    )


@router.message(F.chat.type == ChatType.PRIVATE, ReplyTextFilter(lambda: t("menu.start_over")))
async def reply_start_over(
    message: Message,
    is_admin: bool,
    state: FSMContext | None = None,
) -> None:
    if state is not None:
        await state.clear()
    await send_start(message, is_admin)


# ---------------------------------------------------------------------------
# Menu navigation
# ---------------------------------------------------------------------------


@router.callback_query(MenuCB.filter(F.target == "root"))
async def menu_root(
    query: CallbackQuery,
    is_admin: bool,
    state: FSMContext | None = None,
) -> None:
    if state is not None:
        await state.clear()
    registry = await ready_registry()
    if isinstance(query.message, Message):
        await query.message.edit_text(
            t("menu.title"),
            reply_markup=await main_menu(registry, is_admin=is_admin),
        )
    await query.answer()


@router.message(F.chat.type == ChatType.PRIVATE, Command("help"))
@router.message(F.chat.type == ChatType.PRIVATE, ReplyTextFilter(lambda: t("menu.help")))
async def cmd_help(message: Message, state: FSMContext | None = None) -> None:
    if state is not None:
        await state.clear()
    registry = await ready_registry()
    await message.answer(get_help_body(registry), reply_markup=back_to_menu(registry).as_markup())


@router.callback_query(MenuCB.filter(F.target == "help"))
async def menu_help(query: CallbackQuery) -> None:
    registry = await ready_registry()
    if isinstance(query.message, Message):
        await query.message.edit_text(
            get_help_body(registry), reply_markup=back_to_menu(registry).as_markup()
        )
    await query.answer()


@router.callback_query(NoopCB.filter())
async def noop(query: CallbackQuery) -> None:
    """Page counters and separators land here."""
    await query.answer()


# ---------------------------------------------------------------------------
# Sponsor Membership Verification
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "check_sponsors")
async def verify_sponsors(
    query: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    is_admin: bool,
) -> None:
    """Re-check sponsor membership when user taps «عضو شدم»."""
    from app.core.sponsor_service import get_missing_sponsors
    from app.middlewares.sponsor_gate import build_sponsor_join_keyboard

    registry = await ready_registry()
    user_id = query.from_user.id if query.from_user else 0

    # fresh=True: bypass the 30s membership cache — the user just tapped
    # "I joined", so re-read Telegram instead of the stale gate verdict.
    missing = await get_missing_sponsors(bot, session, user_id, fresh=True)

    if missing:
        log.info(
            "sponsor_verify_still_missing",
            user_id=user_id,
            missing=[c.channel_id for c in missing],
        )
        names = "، ".join(c.title for c in missing[:5])
        await query.answer(
            f"هنوز در این کانال‌ها عضو نشده‌اید:\n{names}",
            show_alert=True,
        )
        if isinstance(query.message, Message):
            kb = build_sponsor_join_keyboard(list(missing), registry)
            prompt = (
                f"{registry.text('lock')} <b>عضویت اجباری در کانال‌های اسپانسر</b>\n\n"
                "برای استفاده از امکانات ربات، لطفاً ابتدا در کانال‌های زیر عضو شوید "
                "و سپس دکمه «عضو شدم» را بزنید:"
            )
            try:
                await query.message.edit_text(prompt, reply_markup=kb.as_markup())
            except TelegramBadRequest:
                # "message is not modified" — the prompt is already showing.
                pass
        return

    log.info("sponsor_verify_confirmed", user_id=user_id)
    await query.answer("عضویت شما تایید شد! خوش آمدید 🎉")
    if isinstance(query.message, Message):
        if query.message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            user_mention = html.quote(query.from_user.first_name or "دوست من")
            await query.message.edit_text(
                f"🎉 <b>عضویت شما تایید شد {user_mention} عزیز!</b>\n\n"
                "حالا می‌توانید لینک‌ها و دستورات خود را در گروه ارسال کنید تا ربات دانلود کند.",
                reply_markup=None,
            )
        else:
            body = t("start.greeting", name=html.quote(query.from_user.first_name or "دوست من"))
            await query.message.edit_text(
                body,
                reply_markup=await main_menu(registry, is_admin=is_admin),
            )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@router.message(F.chat.type == ChatType.PRIVATE, Command("me"))
async def cmd_me(message: Message, session: AsyncSession) -> None:
    """Show the sender's stored profile; falls back gracefully."""
    from app.db.repo import UserRepo

    registry = await ready_registry()
    user_id = message.from_user.id if message.from_user else 0
    user = await UserRepo(session).get(user_id)
    if user is None:
        name = html.quote(message.from_user.first_name if message.from_user else "دوست من")
        body = t("start.greeting", name=name)
    else:
        uname = f"@{user.username}" if user.username else "—"
        premium = "بله" if user.is_premium else "خیر"
        body = (
            "👤 <b>پرونده شما</b>\n\n"
            f"• آیدی: <code>{user.telegram_id}</code>\n"
            f"• یوزرنیم: {html.quote(uname)}\n"
            f"• نام: {html.quote(user.display_name)}\n"
            f"• پرمیوم تلگرام: {premium}"
        )
    await message.answer(body, reply_markup=back_to_menu(registry).as_markup())


@router.message(F.chat.type == ChatType.PRIVATE, Command("ping"))
async def cmd_ping(message: Message) -> None:
    started = time.perf_counter()
    sent = await message.answer(t("common.processing"))
    latency_ms = int((time.perf_counter() - started) * 1000)
    await sent.edit_text(t("ping.pong", latency_ms=latency_ms))


@router.message(F.chat.type == ChatType.PRIVATE, Command("health"), IsAdmin())
async def cmd_health(message: Message, settings: Settings, session: AsyncSession) -> None:
    api_mode = "Local Bot API Server" if settings.uses_local_api else "Public Bot API"
    runtime = await get_settings_store().all()
    slots = await EmojiRepo(session).all()
    premium_slots = sum(1 for s in slots if s.is_premium)

    lines = [
        t("health.title"),
        "",
        t("health.section_api"),
        f"• API: <code>{api_mode}</code>",
        f"• حداکثر دانلود: <code>{human_bytes(settings.download_limit)}</code>",
        f"• حداکثر آپلود: <code>{human_bytes(settings.upload_limit)}</code>",
        "",
        t("health.section_runtime"),
        f"• جاب همزمان: <code>{settings.max_concurrent_jobs}</code>",
        f"• تایم‌اوت جاب: <code>{settings.job_timeout}s</code>",
        "",
        t("health.section_data"),
        f"• اسلات ایموجی: <code>{len(slots)}</code> (پریمیوم: <code>{premium_slots}</code>)",
        f"• کلید تنظیمات: <code>{len(runtime)}</code>",
        f"• ایموجی پریمیوم: <code>{runtime.get('premium_emoji_enabled', '?')}</code>",
        f"• حالت تعمیر: <code>{runtime.get('maintenance', '?')}</code>",
    ]
    await message.answer("\n".join(lines))


feature = register(
    Feature(
        name="basic",
        router=router,
        order=0,
    )
)
