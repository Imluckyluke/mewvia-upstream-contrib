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
from app.media.audio_tools import extract_audio_from_video
from app.media.kinds import resolve_panel_media, send_processing_status
from app.media.probe import probe
from app.media.video_tools import compress_video, make_video_note, mute_video, reverse_video
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.progress import ProgressController
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
            "ویدیومسیج گرد",
            callback_data=VideoToolCB(action="note").pack(),
            style=Style.CONFIRM,
            emoji_slot="video_note",
            registry=registry,
        ),
        inline_button(
            "فشرده‌سازی ویدیو",
            callback_data=VideoToolCB(action="compress_menu").pack(),
            style=Style.PRIMARY,
            emoji_slot="compress",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "بی‌صدا کردن",
            callback_data=VideoToolCB(action="mute").pack(),
            style=Style.PRIMARY,
            emoji_slot="mute",
            registry=registry,
        ),
        inline_button(
            "معکوس کردن",
            callback_data=VideoToolCB(action="reverse").pack(),
            style=Style.PRIMARY,
            emoji_slot="reverse",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "استخراج صدا از ویدیو",
            callback_data=VideoToolCB(action="extract").pack(),
            style=Style.CONFIRM,
            emoji_slot="audio",
            registry=registry,
        ),
        inline_button(
            "🔍 تشخیص موزیک (Shazam)",
            callback_data=VideoToolCB(action="shazam").pack(),
            style=Style.PRIMARY,
            emoji_slot="shazam",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            t("common.back"),
            callback_data="menu:root",
            style=Style.DESTRUCTIVE,
            emoji_slot="back",
            registry=registry,
        )
    )

    if isinstance(query.message, Message):
        prompt_v_text = (
            f"{registry.text('video')} <b>ابزارهای ویدیو</b>\n\n"
            "عملیات مورد نظر را روی ویدیو انتخاب کن:"
        )
        if query.message.text is not None:
            await query.message.edit_text(
                prompt_v_text,
                reply_markup=kb.as_markup(),
            )
        else:
            sent_p = await query.message.reply(
                prompt_v_text,
                reply_markup=kb.as_markup(),
            )
            resolved = await resolve_panel_media(query)
            if resolved and sent_p:
                kind, file_id, file_size, reply_to_id = resolved
                user_id = query.from_user.id if query.from_user else 0
                user_name = query.from_user.first_name if query.from_user else "کاربر"
                from app.middlewares.group_guard import store_panel

                await store_panel(
                    query.message.chat.id,
                    sent_p.message_id,
                    owner_id=user_id,
                    owner_name=user_name,
                    reply_to_message_id=reply_to_id,
                    kind=kind.value,
                    file_id=file_id,
                    file_size=file_size,
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
        lvl_prompt = f"{registry.text('compress')} <b>میزان فشرده‌سازی را انتخاب کن:</b>"
        if query.message.text is not None:
            await query.message.edit_text(
                lvl_prompt,
                reply_markup=kb.as_markup(),
            )
        else:
            sent_p = await query.message.reply(
                lvl_prompt,
                reply_markup=kb.as_markup(),
            )
            resolved = await resolve_panel_media(query)
            if resolved and sent_p:
                kind, file_id, file_size, reply_to_id = resolved
                user_id = query.from_user.id if query.from_user else 0
                user_name = query.from_user.first_name if query.from_user else "کاربر"
                from app.middlewares.group_guard import store_panel

                await store_panel(
                    query.message.chat.id,
                    sent_p.message_id,
                    owner_id=user_id,
                    owner_name=user_name,
                    reply_to_message_id=reply_to_id,
                    kind=kind.value,
                    file_id=file_id,
                    file_size=file_size,
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

    if action == "shazam":
        from app.features.shazam.router import execute_shazam_flow

        await execute_shazam_flow(msg, file_id, reply_to_id, bot, session, settings)
        return

    stage_desc = {
        "note": "در حال ساخت ویدیومسیج گرد…",
        "compress": "در حال فشرده‌سازی ویدیو…",
        "mute": "در حال حذف صدای ویدیو…",
        "reverse": "در حال معکوس کردن ویدیو…",
        "extract": "در حال استخراج صدای ویدیو…",
    }.get(action, "در حال پردازش ویدیو…")

    progress: ProgressController | Message | None = None
    try:
        progress = await ProgressController.start(
            bot=bot,
            chat_id=msg.chat.id,
            reply_to_id=reply_to_id or msg.message_id,
            initial_stage=stage_desc,
            session=session,
            replace_msg=msg if msg.text is not None else None,
        )
    except Exception as p_exc:
        log.warning("progress_controller_video_start_failed", error=str(p_exc))
        progress = await send_processing_status(msg, t("common.processing"))

    status_msg = progress

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

            registry = await ready_registry()
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
                cap_cmp = (
                    f"{registry.text('compress')} <b>فشرده‌سازی انجام شد!</b>\n"
                    f"حجم قبلی: {old_kb}KB ➔ جدید: {new_kb}KB"
                )
                await bot.send_video(
                    chat_id=msg.chat.id,
                    video=out_obj,
                    reply_to_message_id=reply_to_id,
                    caption=cap_cmp,
                )

            elif action == "mute":
                res_info = await mute_video(src_file, out_file)
                out_obj = FSInputFile(out_file)
                await bot.send_video(
                    chat_id=msg.chat.id,
                    video=out_obj,
                    reply_to_message_id=reply_to_id,
                    caption=f"{registry.text('mute')} <b>صدا با موفقیت حذف شد.</b>",
                )

            elif action == "reverse":
                res_info = await reverse_video(src_file, out_file, info)
                out_obj = FSInputFile(out_file)
                await bot.send_video(
                    chat_id=msg.chat.id,
                    video=out_obj,
                    reply_to_message_id=reply_to_id,
                    caption=f"{registry.text('reverse')} <b>ویدیو معکوس شد.</b>",
                )

            elif action == "extract":
                out_file = work_dir / "extracted.mp3"
                await extract_audio_from_video(src_file, out_file)
                out_obj = FSInputFile(out_file)
                await bot.send_audio(
                    chat_id=msg.chat.id,
                    audio=out_obj,
                    reply_to_message_id=reply_to_id,
                    title="Extracted Audio",
                    caption=(
                        f"{registry.text('audio')} <b>صدای استخراج‌شده از ویدیو</b>\n\n"
                        f"دانلود شده با @{settings.bot_username} {registry.text('paw')}"
                    ),
                )
                res_info = await probe(out_file)

            else:
                return

            if status_msg is not None:
                try:
                    await status_msg.delete()
                except Exception:
                    pass

            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            target_id = "atool_extract" if action == "extract" else f"vtool_{action}"
            await conv_repo.log_conversion(
                telegram_id=user_id,
                source_kind=kind.value,
                target_id=target_id,
                in_bytes=file_size,
                out_bytes=res_info.file_size,
                duration_ms=elapsed_ms,
                status="success",
            )

        except MewviaError as exc:
            if status_msg is not None:
                err_msg = str(exc)
                if any("\u0600" <= c <= "\u06ff" for c in err_msg):
                    await status_msg.edit_text(f"❌ <b>خطا:</b> {err_msg}")
                else:
                    await status_msg.edit_text(error_text(exc.code))
        except Exception as exc:
            log.error("vtool_unexpected_error", error=str(exc))
            if status_msg is not None:
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
