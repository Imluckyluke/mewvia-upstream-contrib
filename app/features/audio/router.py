"""Audio tools feature router."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import AudioToolCB, MenuCB
from app.config import Settings
from app.core.errors import MewviaError
from app.core.logging import get_logger
from app.core.quota import check_daily_quota
from app.db.repo import ConversionRepo
from app.features.registry import Feature, register
from app.media.audio_tools import (
    change_audio_speed,
    convert_to_mp3,
    convert_to_voice_note,
    extract_audio_from_video,
)
from app.media.kinds import (
    MediaKind,
    detect_media_kind,
    resolve_panel_media,
)
from app.middlewares.group_guard import store_panel
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.styles import Style
from app.ui.texts import error_text, t

log = get_logger(__name__)

router = Router(name="audio")


async def ready_registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


@router.message(F.chat.type == "private", F.voice | F.audio)
async def handle_incoming_audio(message: Message) -> None:
    registry = await ready_registry()
    kb = KB(registry)

    if message.voice:
        kb.row(
            inline_button(
                "🎵 تبدیل ویس به MP3",
                callback_data=AudioToolCB(action="to_mp3").pack(),
                style=Style.PRIMARY,
                emoji_slot="audio",
                registry=registry,
            ),
            inline_button(
                "⚡ تغییر سرعت ویس",
                callback_data=AudioToolCB(action="speed_menu").pack(),
                style=Style.CONFIRM,
                emoji_slot="clock",
                registry=registry,
            ),
        )
    elif message.audio:
        kb.row(
            inline_button(
                "🎤 تبدیل موزیک به ویس (Voice)",
                callback_data=AudioToolCB(action="to_voice").pack(),
                style=Style.CONFIRM,
                emoji_slot="audio",
                registry=registry,
            ),
            inline_button(
                "⚡ تغییر سرعت موزیک",
                callback_data=AudioToolCB(action="speed_menu").pack(),
                style=Style.PRIMARY,
                emoji_slot="clock",
                registry=registry,
            ),
        )

    if message.audio:
        kb.row(
            inline_button(
                "🎵 ویرایش تگ و کاور",
                callback_data=AudioToolCB(action="tag_menu").pack(),
                style=Style.CONFIRM,
                emoji_slot="edit",
                registry=registry,
            )
        )

    kb.row(
        inline_button(
            t("common.cancel"),
            callback_data="menu:root",
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        )
    )

    sent_msg = await message.reply(
        "<b>🎵 ابزارهای صدا و ویس</b>\n\nعملیات مورد نظر را انتخاب کن:",
        reply_markup=kb.as_markup(),
    )

    detected = detect_media_kind(message)
    if detected:
        kind, _obj, file_id, file_size = detected
        owner_id = message.from_user.id if message.from_user else 0
        owner_name = message.from_user.first_name if message.from_user else "کاربر"
        await store_panel(
            chat_id=message.chat.id,
            message_id=sent_msg.message_id,
            owner_id=owner_id,
            owner_name=owner_name,
            reply_to_message_id=message.message_id,
            kind=kind.value,
            file_id=file_id,
            file_size=file_size,
        )


@router.callback_query(AudioToolCB.filter(F.action == "speed_menu"))
async def audio_speed_menu(query: CallbackQuery) -> None:
    registry = await ready_registry()
    kb = KB(registry)

    kb.row(
        inline_button(
            "0.5x (آهسته)",
            callback_data=AudioToolCB(action="set_speed", speed="0.5").pack(),
            style=Style.PRIMARY,
            registry=registry,
        ),
        inline_button(
            "1.25x",
            callback_data=AudioToolCB(action="set_speed", speed="1.25").pack(),
            style=Style.PRIMARY,
            registry=registry,
        ),
        inline_button(
            "1.5x (تند)",
            callback_data=AudioToolCB(action="set_speed", speed="1.5").pack(),
            style=Style.CONFIRM,
            registry=registry,
        ),
        inline_button(
            "2.0x (خیلی تند)",
            callback_data=AudioToolCB(action="set_speed", speed="2.0").pack(),
            style=Style.PRIMARY,
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
            "<b>⚡ سرعت مورد نظر را انتخاب کن:</b>", reply_markup=kb.as_markup()
        )
    await query.answer()


@router.callback_query(AudioToolCB.filter(F.action != "tag_menu"))
async def process_audio_tool(
    query: CallbackQuery,
    callback_data: AudioToolCB,
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

    if action != "tag_menu" and not await check_daily_quota(session, user_id, query):
        return

    await query.answer()

    status_edited = await msg.edit_text(t("common.processing"), reply_markup=None)
    status_msg: Message | None = status_edited if isinstance(status_edited, Message) else None

    start_time = time.perf_counter()
    conv_repo = ConversionRepo(session)

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir:
        work_dir = Path(tmp_dir)
        src_file = work_dir / "src_audio"

        try:
            await bot.download(file_id, destination=src_file)

            if action == "to_voice":
                out_file = work_dir / "voice.ogg"
                res_info = await convert_to_voice_note(src_file, out_file)
                out_obj = FSInputFile(out_file)
                await bot.send_voice(
                    chat_id=msg.chat.id,
                    voice=out_obj,
                    reply_to_message_id=reply_to_id,
                )

            elif action == "to_mp3":
                out_file = work_dir / "audio.mp3"
                res_info = await convert_to_mp3(src_file, out_file)
                out_obj = FSInputFile(out_file)
                await bot.send_audio(
                    chat_id=msg.chat.id,
                    audio=out_obj,
                    reply_to_message_id=reply_to_id,
                    title="Converted Audio",
                )

            elif action == "extract":
                out_file = work_dir / "extracted.mp3"
                res_info = await extract_audio_from_video(src_file, out_file)
                out_obj = FSInputFile(out_file)
                await bot.send_audio(
                    chat_id=msg.chat.id,
                    audio=out_obj,
                    reply_to_message_id=reply_to_id,
                    title="Extracted Audio",
                )

            elif action == "set_speed":
                speed = float(callback_data.speed or "1.5")
                is_voice = kind == MediaKind.VOICE
                if is_voice:
                    out_file = work_dir / "speed_voice.ogg"
                    res_info = await change_audio_speed(
                        src_file, out_file, speed_factor=speed, to_voice=True
                    )
                    out_obj = FSInputFile(out_file)
                    await bot.send_voice(
                        chat_id=msg.chat.id,
                        voice=out_obj,
                        reply_to_message_id=reply_to_id,
                    )
                else:
                    out_file = work_dir / "speed_audio.mp3"
                    res_info = await change_audio_speed(
                        src_file, out_file, speed_factor=speed, to_voice=False
                    )
                    out_obj = FSInputFile(out_file)
                    await bot.send_audio(
                        chat_id=msg.chat.id,
                        audio=out_obj,
                        reply_to_message_id=reply_to_id,
                        title=f"Speed {speed}x",
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
                target_id=f"atool_{action}",
                in_bytes=file_size,
                out_bytes=res_info.file_size,
                duration_ms=elapsed_ms,
                status="success",
            )

        except MewviaError as exc:
            if status_msg:
                await status_msg.edit_text(error_text(exc.code))
        except Exception as exc:
            log.error("atool_unexpected_error", error=str(exc))
            if status_msg:
                await status_msg.edit_text(error_text("unknown"))


@router.callback_query(MenuCB.filter(F.target == "audio"))
async def menu_audio(query: CallbackQuery) -> None:
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
            "<b>🎵 ابزارهای صدا و ویس</b>\n\n"
            "یک ویس یا فایل صوتی بفرست و از منو گزینه مورد نظر را انتخاب کن:\n"
            "• تبدیل موزیک به ویس تلگرام (Voice)\n"
            "• تبدیل ویس به فایل صوتی MP3\n"
            "• تغییر سرعت ویس و موزیک (0.5x تا 2.0x)\n"
            "• ویرایش تگ و کاور موزیک (نام، خواننده، آلبوم، کاور) 🎵",
            reply_markup=kb,
        )
    await query.answer()


feature = register(
    Feature(
        name="audio",
        router=router,
        menu_label_key="menu.audio",
        menu_emoji_slot="audio",
        menu_style=Style.PRIMARY,
        order=130,
    )
)
