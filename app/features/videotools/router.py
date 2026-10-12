"""Video tools feature router."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import MenuCB, VideoToolCB
from app.config import Settings
from app.core.errors import MewviaError
from app.core.logging import get_logger
from app.core.quota import check_daily_quota
from app.db.repo import ConversionRepo
from app.features.registry import Feature, register
from app.media.kinds import resolve_panel_media
from app.media.probe import probe
from app.media.video_tools import compress_video, make_video_note, mute_video, reverse_video
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.styles import Style
from app.ui.texts import error_text, t

log = get_logger(__name__)

router = Router(name="videotools")


async def ready_registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


@router.callback_query(F.data == "vtool_action:start")
async def start_vtool_menu(query: CallbackQuery) -> None:
    registry = await ready_registry()
    kb = KB(registry)

    kb.row(
        inline_button(
            "🔴 ویدیومسیج گرد",
            callback_data=VideoToolCB(action="note").pack(),
            style=Style.CONFIRM,
            emoji_slot="video",
            registry=registry,
        ),
        inline_button(
            "📉 فشرده‌سازی ویدیو",
            callback_data=VideoToolCB(action="compress_menu").pack(),
            style=Style.PRIMARY,
            emoji_slot="download",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "🔇 بی‌صدا کردن",
            callback_data=VideoToolCB(action="mute").pack(),
            style=Style.PRIMARY,
            emoji_slot="audio",
            registry=registry,
        ),
        inline_button(
            "🔄 معکوس کردن",
            callback_data=VideoToolCB(action="reverse").pack(),
            style=Style.PRIMARY,
            emoji_slot="refresh",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            t("common.back"),
            callback_data="menu:root",
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        )
    )

    if isinstance(query.message, Message):
        await query.message.edit_text(
            "<b>🎬 ابزارهای ویدیو</b>\n\nعملیات مورد نظر را روی ویدیو انتخاب کن:",
            reply_markup=kb.as_markup(),
        )
    await query.answer()


@router.callback_query(VideoToolCB.filter(F.action == "compress_menu"))
async def compress_level_menu(query: CallbackQuery) -> None:
    registry = await ready_registry()
    kb = KB(registry)

    kb.row(
        inline_button(
            "کم (CRF 32)",
            callback_data=VideoToolCB(action="compress", level="low").pack(),
            style=Style.PRIMARY,
            registry=registry,
        ),
        inline_button(
            "متوسط (CRF 26)",
            callback_data=VideoToolCB(action="compress", level="medium").pack(),
            style=Style.CONFIRM,
            registry=registry,
        ),
        inline_button(
            "بالا (CRF 22)",
            callback_data=VideoToolCB(action="compress", level="high").pack(),
            style=Style.PRIMARY,
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            t("common.back"),
            callback_data="vtool_action:start",
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        )
    )

    if isinstance(query.message, Message):
        await query.message.edit_text(
            "<b>📉 میزان فشرده‌سازی را انتخاب کن:</b>", reply_markup=kb.as_markup()
        )
    await query.answer()


@router.callback_query(VideoToolCB.filter())
async def process_video_tool(
    query: CallbackQuery,
    callback_data: VideoToolCB,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    msg = query.message
    resolved = await resolve_panel_media(query)

    if not resolved:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    kind, file_id, file_size, reply_to_id = resolved
    user_id = query.from_user.id
    action = callback_data.action

    if not await check_daily_quota(session, user_id, query):
        return

    await query.answer()

    status_edited = await msg.edit_text(t("common.processing"), reply_markup=None)
    status_msg: Message | None = status_edited if isinstance(status_edited, Message) else None

    start_time = time.perf_counter()
    conv_repo = ConversionRepo(session)

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir:
        work_dir = Path(tmp_dir)
        src_file = work_dir / "src_video.mp4"
        out_file = work_dir / "output.mp4"

        try:
            await bot.download(file_id, destination=src_file)
            info = await probe(src_file)

            if action == "note":
                out_file = work_dir / "video_note.mp4"
                res_info = await make_video_note(src_file, out_file, info)
                out_obj = FSInputFile(out_file)
                await bot.send_video_note(
                    chat_id=msg.chat.id,
                    video_note=out_obj,
                    reply_to_message_id=reply_to_id,
                )

            elif action == "compress":
                res_info = await compress_video(src_file, out_file, info, level=callback_data.level)
                out_obj = FSInputFile(out_file)
                old_kb = info.file_size // 1024
                new_kb = res_info.file_size // 1024
                await bot.send_video(
                    chat_id=msg.chat.id,
                    video=out_obj,
                    reply_to_message_id=reply_to_id,
                    caption=(
                        f"📉 <b>فشرده‌سازی انجام شد!</b>\nحجم قبلی: {old_kb}KB ➔ جدید: {new_kb}KB"
                    ),
                )

            elif action == "mute":
                res_info = await mute_video(src_file, out_file)
                out_obj = FSInputFile(out_file)
                await bot.send_video(
                    chat_id=msg.chat.id,
                    video=out_obj,
                    reply_to_message_id=reply_to_id,
                    caption="🔇 <b>صدا با موفقیت حذف شد.</b>",
                )

            elif action == "reverse":
                res_info = await reverse_video(src_file, out_file, info)
                out_obj = FSInputFile(out_file)
                await bot.send_video(
                    chat_id=msg.chat.id,
                    video=out_obj,
                    reply_to_message_id=reply_to_id,
                    caption="🔄 <b>ویدیو معکوس شد.</b>",
                )

            else:
                return

            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass

            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            await conv_repo.log_conversion(
                telegram_id=user_id,
                source_kind=kind.value,
                target_id=f"vtool_{action}",
                in_bytes=file_size,
                out_bytes=res_info.file_size,
                duration_ms=elapsed_ms,
                status="success",
            )

        except MewviaError as exc:
            if status_msg:
                await status_msg.edit_text(error_text(exc.code))
        except Exception as exc:
            log.error("vtool_unexpected_error", error=str(exc))
            if status_msg:
                await status_msg.edit_text(error_text("unknown"))


@router.callback_query(MenuCB.filter(F.target == "videotools"))
async def menu_videotools(query: CallbackQuery) -> None:
    registry = await ready_registry()
    kb = (
        KB(registry)
        .button(
            t("common.back"),
            callback_data=MenuCB(target="root").pack(),
            style=Style.PRIMARY,
            emoji_slot="back",
        )
        .as_markup()
    )
    if isinstance(query.message, Message):
        await query.message.edit_text(
            "<b>🎬 ابزارهای ویدیو و ویدیومسیج</b>\n\n"
            "برای استفاده، یک ویدیو یا ویدیو مسیج بفرست برای پیشی "
            "و از منوی باز شده گزینه مورد نظرت رو انتخاب کن:\n"
            "• ساخت ویدیومسیج گرد دایره‌ای یا برعکس\n"
            "• فشرده‌سازی ویدیو\n"
            "• بی‌صدا کردن و معکوس کردن",
            reply_markup=kb,
        )
    await query.answer()


feature = register(
    Feature(
        name="videotools",
        router=router,
        menu_label_key="menu.videotools",
        menu_emoji_slot="video",
        menu_style=Style.PRIMARY,
        order=120,
    )
)
