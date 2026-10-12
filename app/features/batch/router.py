"""Batch Transformation Feature Router.

Provides an interactive inline menu allowing users to toggle and combine multiple operations
(Wide, Filters, Text, Target Format) before executing them all at once.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import BatchCB
from app.config import Settings
from app.core.errors import MewviaError
from app.core.logging import get_logger
from app.core.quota import check_daily_quota
from app.db.repo import ConversionRepo
from app.features.registry import Feature, register
from app.features.textoverlay.router import TextOverlayFSM
from app.media.chained_pipeline import process_batch_media
from app.media.filters import FILTER_MAP
from app.media.kinds import MediaKind, resolve_panel_media
from app.media.specs import (
    TARGET_GIF,
    TARGET_PNG,
    TARGET_WEBM_STICKER,
    TARGET_WEBP,
    allowed_targets_for,
    get_target,
)
from app.middlewares.group_guard import get_panel, get_panel_opts, update_panel_opts
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.styles import Style
from app.ui.texts import error_text, t

log = get_logger(__name__)

router = Router(name="batch")

_BATCH_MENU_TITLE = (
    "<b>⚙️ تغییرات دسته‌ای (ادغام چند عملیات)</b>\n\n"
    "تغییرات مورد نظر را فعال کرده و در نهایت دکمه اعمال را بزنید:"
)


async def ready_registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


async def render_batch_menu(bot: Bot, chat_id: int, message_id: int) -> None:
    """Render (or re-render) the batch menu on the panel message via the Bot API."""
    registry = await ready_registry()
    opts = await get_panel_opts(chat_id, message_id)
    kb = KB(registry)

    # Status indicators
    check_wide = "✅" if opts.get("wide") else ""

    filter_id = opts.get("filter_id")
    check_flt = (
        f"✅ ({FILTER_MAP[filter_id].label})" if filter_id and filter_id in FILTER_MAP else ""
    )

    text_data = opts.get("text")
    check_txt = "✅" if text_data and text_data.get("text") else ""

    target_id = opts.get("target")
    target_spec = get_target(target_id) if target_id else None
    target_label = t(target_spec.label_key) if target_spec else "انتخاب فرمت خروجی"
    check_fmt = f"✅ ({target_label})" if target_id else ""

    kb.row(
        inline_button(
            f"↔️ واید کردن {check_wide}",
            callback_data=BatchCB(action="toggle_wide").pack(),
            style=Style.PRIMARY if not opts.get("wide") else Style.CONFIRM,
            emoji_slot="wide" if not check_wide else "ok",
            registry=registry,
        ),
        inline_button(
            f"🎨 فیلتر {check_flt}",
            callback_data=BatchCB(action="filter_menu").pack(),
            style=Style.PRIMARY if not filter_id else Style.CONFIRM,
            emoji_slot="filter" if not check_flt else "ok",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            f"📝 متن / استیکرمانی {check_txt}",
            callback_data=BatchCB(action="text_menu").pack(),
            style=Style.PRIMARY if not text_data else Style.CONFIRM,
            emoji_slot="text" if not check_txt else "ok",
            registry=registry,
        ),
        inline_button(
            f"🎯 فرمت {check_fmt}",
            callback_data=BatchCB(action="target_menu").pack(),
            style=Style.PRIMARY if not target_id else Style.CONFIRM,
            emoji_slot="download" if not check_fmt else "ok",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "❌ لغو و پاکسازی همه",
            callback_data=BatchCB(action="clear").pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        ),
        inline_button(
            "🚀 اعمال تغییرات",
            callback_data=BatchCB(action="apply").pack(),
            style=Style.CONFIRM,
            emoji_slot="lightning",
            registry=registry,
        ),
    )

    try:
        await bot.edit_message_text(
            _BATCH_MENU_TITLE,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=kb.as_markup(),
        )
    except TelegramBadRequest:
        # "message is not modified" — the menu is already showing exactly this state.
        pass


@router.callback_query(BatchCB.filter(F.action == "menu"))
async def show_batch_menu(query: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return
    resolved = await resolve_panel_media(query)
    if not resolved:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    await render_batch_menu(bot, query.message.chat.id, query.message.message_id)
    await query.answer()


@router.callback_query(BatchCB.filter(F.action == "toggle_wide"))
async def toggle_wide_opt(query: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return
    resolved = await resolve_panel_media(query)
    if not resolved:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    chat_id, message_id = query.message.chat.id, query.message.message_id
    opts = await get_panel_opts(chat_id, message_id)
    await update_panel_opts(chat_id, message_id, wide=not opts.get("wide", False))
    await render_batch_menu(bot, chat_id, message_id)
    await query.answer()


@router.callback_query(BatchCB.filter(F.action == "clear"))
async def clear_opts(query: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return
    resolved = await resolve_panel_media(query)
    if not resolved:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    chat_id, message_id = query.message.chat.id, query.message.message_id
    await update_panel_opts(chat_id, message_id, wide=False, filter_id=None, text=None, target=None)
    await render_batch_menu(bot, chat_id, message_id)
    await query.answer("تمام تنظیمات پاک شد.")


@router.callback_query(BatchCB.filter(F.action == "filter_menu"))
async def show_batch_filter_menu(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return
    registry = await ready_registry()
    kb = KB(registry)

    for f_id, spec in FILTER_MAP.items():
        kb.button(
            spec.label,
            callback_data=BatchCB(action="set_filter", val=f_id).pack(),
            style=Style.PRIMARY,
            emoji_slot=spec.emoji_slot,
        )
    kb.adjust(2, 2, 1)
    kb.row(
        inline_button(
            t("common.back"),
            callback_data=BatchCB(action="menu").pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="back",
            registry=registry,
        )
    )

    await query.message.edit_text(
        "<b>🎨 انتخاب فیلتر</b>\n\nفیلتر مورد نظر را برای ترکیب انتخاب کنید:",
        reply_markup=kb.as_markup(),
    )
    await query.answer()


@router.callback_query(BatchCB.filter(F.action == "set_filter"))
async def set_batch_filter(
    query: CallbackQuery, callback_data: BatchCB, bot: Bot, state: FSMContext
) -> None:
    await state.clear()
    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return
    resolved = await resolve_panel_media(query)
    if not resolved:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    chat_id, message_id = query.message.chat.id, query.message.message_id
    await update_panel_opts(chat_id, message_id, filter_id=callback_data.val)
    await render_batch_menu(bot, chat_id, message_id)
    await query.answer()


@router.callback_query(BatchCB.filter(F.action == "target_menu"))
async def show_batch_target_menu(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return
    resolved = await resolve_panel_media(query)
    if not resolved:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    registry = await ready_registry()
    kb = KB(registry)
    kind = MediaKind(resolved[0].value)

    targets = allowed_targets_for(kind)
    for t_spec in targets:
        kb.button(
            t(t_spec.label_key),
            callback_data=BatchCB(action="set_target", val=t_spec.id).pack(),
            style=t_spec.style,
            emoji_slot=t_spec.emoji_slot,
        )
    kb.adjust(*([2] * (len(targets) // 2) + ([1] if len(targets) % 2 else [])))
    kb.row(
        inline_button(
            t("common.back"),
            callback_data=BatchCB(action="menu").pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="back",
            registry=registry,
        )
    )

    await query.message.edit_text(
        "<b>🎯 انتخاب فرمت خروجی</b>\n\nفرمت نهایی فایل ترکیبی را مشخص کنید:",
        reply_markup=kb.as_markup(),
    )
    await query.answer()


@router.callback_query(BatchCB.filter(F.action == "set_target"))
async def set_batch_target(
    query: CallbackQuery, callback_data: BatchCB, bot: Bot, state: FSMContext
) -> None:
    await state.clear()
    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return
    resolved = await resolve_panel_media(query)
    if not resolved:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    chat_id, message_id = query.message.chat.id, query.message.message_id
    await update_panel_opts(chat_id, message_id, target=callback_data.val)
    await render_batch_menu(bot, chat_id, message_id)
    await query.answer()


@router.callback_query(BatchCB.filter(F.action == "text_menu"))
async def show_batch_text_prompt(query: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return
    resolved = await resolve_panel_media(query)
    if not resolved:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    # Fresh state: panel coordinates for the text flow, nothing stale from other flows.
    await state.clear()
    await state.update_data(
        batch_panel_chat_id=query.message.chat.id,
        batch_panel_msg_id=query.message.message_id,
    )
    await state.set_state(TextOverlayFSM.waiting_for_text)

    registry = await ready_registry()
    kb = (
        KB(registry)
        .button(
            t("common.back"),
            callback_data=BatchCB(action="menu").pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="back",
        )
        .as_markup()
    )

    await query.message.edit_text(
        "<b>📝 متن ترکیبی</b>\n\nمتن مورد نظرت را بنویس و بفرست:", reply_markup=kb
    )
    await query.answer()


@router.callback_query(BatchCB.filter(F.action == "apply"))
async def apply_batch_changes(
    query: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    await state.clear()
    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    msg = query.message
    resolved = await resolve_panel_media(query)
    if not resolved:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    kind, file_id, file_size, reply_to_id = resolved

    if kind in (MediaKind.VOICE, MediaKind.AUDIO):
        await query.answer("برای ویس و موزیک از ابزارهای صدا استفاده کن!", show_alert=True)
        return

    opts = await get_panel_opts(msg.chat.id, msg.message_id)

    target_id = opts.get("target")
    if not target_id:
        await query.answer("لطفاً فرمت خروجی را انتخاب کنید!", show_alert=True)
        return

    target_spec = get_target(target_id)
    if not target_spec:
        await query.answer("فرمت نامعتبر است.", show_alert=True)
        return

    panel = await get_panel(msg.chat.id, msg.message_id)
    owner_id = (panel or {}).get("owner_id") or (query.from_user.id if query.from_user else 0)
    if not await check_daily_quota(session, owner_id, query):
        return

    await query.answer()
    status_edited = await msg.edit_text(t("common.processing"), reply_markup=None)
    status_msg: Message | None = status_edited if isinstance(status_edited, Message) else None

    start_time = time.perf_counter()
    conv_repo = ConversionRepo(session)

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir_str:
        work_dir = Path(tmp_dir_str)
        src_file = work_dir / f"batch_src.{kind.value}"

        try:
            await bot.download(file_id, destination=src_file)
            output = await process_batch_media(src_file, kind, target_spec, opts, work_dir)

            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            out_bytes = output.info.file_size
            input_file_obj = FSInputFile(output.path)

            chat_id = msg.chat.id
            if target_spec.id in (TARGET_WEBP.id, TARGET_WEBM_STICKER.id):
                await bot.send_sticker(
                    chat_id=chat_id, sticker=input_file_obj, reply_to_message_id=reply_to_id
                )
            elif target_spec.id == TARGET_GIF.id:
                await bot.send_animation(
                    chat_id=chat_id, animation=input_file_obj, reply_to_message_id=reply_to_id
                )
            elif target_spec.id == TARGET_PNG.id:
                await bot.send_photo(
                    chat_id=chat_id, photo=input_file_obj, reply_to_message_id=reply_to_id
                )
            else:
                await bot.send_document(
                    chat_id=chat_id, document=input_file_obj, reply_to_message_id=reply_to_id
                )

            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass

            await conv_repo.log_conversion(
                telegram_id=query.from_user.id,
                source_kind=kind.value,
                target_id=target_spec.id,
                in_bytes=file_size,
                out_bytes=out_bytes,
                duration_ms=elapsed_ms,
                status="success",
            )
        except MewviaError as exc:
            if status_msg:
                await status_msg.edit_text(error_text(exc.code))
        except Exception as exc:
            log.error("batch_unexpected_error", error=str(exc))
            if status_msg:
                await status_msg.edit_text(error_text("unknown"))


feature = register(
    Feature(
        name="batch",
        router=router,
        order=125,
    )
)
