"""Social media & web media downloader router.

Handles links sent by users:
1. Detects URL from message (YouTube, Instagram, TikTok, Twitter/X, Pinterest, SoundCloud, etc.)
2. Probes metadata and format availability
3. Displays interactive inline keyboard (Video Best, Video 720p, Audio MP3, Gallery Album)
4. Downloads within temporary sandbox under process-wide concurrency & quota controls
5. Sends media back to the user with custom captions and thumbnails
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router, html
from aiogram.filters import BaseFilter, Command, CommandObject, StateFilter
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InputMediaPhoto,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks.downloader import DownloaderCB
from app.config import Settings
from app.core.errors import ConversionError, MewviaError
from app.core.logging import get_logger
from app.core.quota import check_daily_quota
from app.core.units import fa_digits, human_bytes
from app.db.repo import ConversionRepo
from app.features.registry import Feature, register
from app.media.downloader import (
    create_token,
    detect_platform,
    download_audio,
    download_gallery,
    download_video,
    extract_first_url,
    get_download_context,
    probe_media_url,
    store_download_context,
)
from app.media.jobs import run_media_job
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.styles import Style
from app.ui.texts import error_text, t

log = get_logger(__name__)

router = Router(name="downloader")


async def ready_registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


async def _handle_url_input(message: Message, url: str) -> None:
    """Analyze the given URL and present format/quality options."""
    registry = await ready_registry()
    status = await message.reply("⏳ <b>در حال دریافت اطلاعات لینک…</b>")
    status_msg: Message | None = status if isinstance(status, Message) else None

    try:
        info = await probe_media_url(url)
    except MewviaError as exc:
        err = error_text(exc.code)
        if status_msg:
            await status_msg.edit_text(err)
        else:
            await message.answer(err)
        return
    except Exception as exc:
        log.warning("downloader_probe_error", url=url, error=str(exc))
        err = (
            "❌ نتوانستم اطلاعات این لینک را دریافت کنم.\n"
            "ممکن است لینک خصوصی باشد، یا این سایت پشتیبانی نشود."
        )
        if status_msg:
            await status_msg.edit_text(err)
        else:
            await message.answer(err)
        return

    token = create_token()
    await store_download_context(
        token,
        {
            "url": url,
            "platform_key": info.platform_key,
            "platform_name": info.platform_name,
            "title": info.title,
            "uploader": info.uploader,
            "duration_str": info.duration_str,
            "reply_to_id": message.message_id,
        },
    )

    kb = KB(registry)

    if info.is_gallery:
        kb.row(
            inline_button(
                "📸 دانلود تمام تصاویر (آلبوم)",
                callback_data=DownloaderCB(action="gallery", token=token).pack(),
                style=Style.CONFIRM,
                emoji_slot="photo",
                registry=registry,
            )
        )
    elif info.is_audio_only:
        kb.row(
            inline_button(
                "🎵 دانلود آهنگ (MP3)",
                callback_data=DownloaderCB(action="audio", token=token).pack(),
                style=Style.CONFIRM,
                emoji_slot="audio",
                registry=registry,
            )
        )
    else:
        # Standard video post (Reels, YouTube, TikTok, etc.)
        kb.row(
            inline_button(
                "🎬 دانلود ویدیو (بهترین کیفیت)",
                callback_data=DownloaderCB(action="video", token=token).pack(),
                style=Style.PRIMARY,
                emoji_slot="video",
                registry=registry,
            )
        )
        if info.duration_seconds and info.duration_seconds > 60:
            # Offer 720p for longer videos to save data/time
            kb.row(
                inline_button(
                    "📱 کیفیت 720p (سریع‌تر)",
                    callback_data=DownloaderCB(action="video_720", token=token).pack(),
                    style=Style.NEUTRAL,
                    emoji_slot="video",
                    registry=registry,
                )
            )
        kb.row(
            inline_button(
                "🎵 استخراج صدا (MP3)",
                callback_data=DownloaderCB(action="audio", token=token).pack(),
                style=Style.CONFIRM,
                emoji_slot="audio",
                registry=registry,
            )
        )

    kb.row(
        inline_button(
            t("common.cancel"),
            callback_data=DownloaderCB(action="cancel", token=token).pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        )
    )

    uploader_line = f"👤 <b>سازنده:</b> {html.quote(info.uploader)}\n" if info.uploader else ""
    duration_line = (
        f"⏱ <b>مدت زمان:</b> <code>{fa_digits(info.duration_str)}</code>\n"
        if info.duration_seconds
        else ""
    )

    card_text = (
        f"{info.platform_icon} <b>دانلود از {info.platform_name}</b>\n\n"
        f"📝 <b>عنوان:</b> {html.quote(info.title)}\n"
        f"{uploader_line}"
        f"{duration_line}\n"
        "کیفیت یا فرمت مورد نظرت را انتخاب کن:"
    )

    if status_msg:
        await status_msg.edit_text(card_text, reply_markup=kb.as_markup())
    else:
        await message.answer(card_text, reply_markup=kb.as_markup())


@router.message(Command("dl"))
async def cmd_download_url(message: Message, command: CommandObject) -> None:
    """Download media from command: `/dl <url>`."""
    arg_url = (command.args or "").strip()
    url = extract_first_url(arg_url)
    if not url:
        await message.reply(
            "لطفاً لینک مورد نظرت را به این شکل بفرست:\n"
            "<code>/dl https://instagram.com/reel/...</code>"
        )
        return
    await _handle_url_input(message, url)


class ContainsMediaUrlFilter(BaseFilter):
    """Filter that matches only when the message contains a supported media URL."""

    async def __call__(self, message: Message) -> bool | dict[str, Any]:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return False
        url = extract_first_url(text)
        if not url:
            return False
        key, _name, _icon = detect_platform(url)
        if key == "other" and not (url.startswith("http://") or url.startswith("https://")):
            return False
        return {"extracted_url": url}


@router.message(
    F.chat.type == "private",
    StateFilter(None),
    ContainsMediaUrlFilter(),
)
async def auto_detect_url_message(message: Message, extracted_url: str) -> None:
    """Automatically catch URLs sent as private chat messages in idle state."""
    await _handle_url_input(message, extracted_url)


@router.callback_query(DownloaderCB.filter(F.action == "cancel"))
async def cancel_download(query: CallbackQuery) -> None:
    if isinstance(query.message, Message):
        edited = await query.message.edit_text("دانلود لغو شد.")
        if edited is None:
            await query.message.answer("دانلود لغو شد.")
    await query.answer()


@router.callback_query(DownloaderCB.filter())
async def execute_download(
    query: CallbackQuery,
    callback_data: DownloaderCB,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    action = callback_data.action
    token = callback_data.token

    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    msg = query.message
    ctx = await get_download_context(token)
    if not ctx:
        await query.answer(
            "این درخواست منقضی شده است. لطفاً لینک را دوباره بفرستید.", show_alert=True
        )
        return

    user_id = query.from_user.id if query.from_user else 0
    if not await check_daily_quota(session, user_id, query):
        return

    await query.answer()
    status = await msg.edit_text("⏳ <b>در حال دانلود رسانه از سرور…</b>", reply_markup=None)
    status_msg: Message | None = status if isinstance(status, Message) else None

    url = str(ctx.get("url", ""))
    platform_key = str(ctx.get("platform_key", "other"))
    title = str(ctx.get("title", "media"))
    reply_to_id = ctx.get("reply_to_id")
    chat_id = msg.chat.id
    start_time = time.perf_counter()

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir:
        work_dir = Path(tmp_dir)

        try:
            if action in ("video", "video_720"):
                max_h = 720 if action == "video_720" else None

                async def _do_video() -> Path:
                    return await download_video(
                        url,
                        work_dir,
                        max_height=max_h,
                        max_bytes=settings.upload_limit,
                    )

                downloaded_file = await run_media_job(settings, _do_video, stage="download_video")
                file_size = downloaded_file.stat().st_size

                if file_size > settings.upload_limit:
                    limit_str = human_bytes(settings.upload_limit)
                    size_str = human_bytes(file_size)
                    await msg.answer(
                        f"❌ حجم ویدیو ({size_str}) بیشتر از سقف مجاز ({limit_str}) است."
                    )
                    return

                caption = (
                    f"🎬 <b>{html.quote(title)}</b>\n\nدانلود شده با @{settings.bot_username} 🐾"
                )
                await bot.send_video(
                    chat_id=chat_id,
                    video=FSInputFile(downloaded_file),
                    caption=caption[:1024],
                    reply_to_message_id=reply_to_id,
                )

            elif action == "audio":

                async def _do_audio() -> Path:
                    return await download_audio(
                        url,
                        work_dir,
                        max_bytes=settings.upload_limit,
                    )

                downloaded_file = await run_media_job(settings, _do_audio, stage="download_audio")
                file_size = downloaded_file.stat().st_size

                if file_size > settings.upload_limit:
                    limit_str = human_bytes(settings.upload_limit)
                    size_str = human_bytes(file_size)
                    await msg.answer(
                        f"❌ حجم فایل صوتی ({size_str}) بیشتر از سقف مجاز ({limit_str}) است."
                    )
                    return

                uploader = ctx.get("uploader") or "Mewvia"
                caption = (
                    f"🎵 <b>{html.quote(title)}</b>\n\nدانلود شده با @{settings.bot_username} 🐾"
                )
                await bot.send_audio(
                    chat_id=chat_id,
                    audio=FSInputFile(downloaded_file),
                    title=title[:64],
                    performer=str(uploader)[:64],
                    caption=caption[:1024],
                    reply_to_message_id=reply_to_id,
                )

            elif action == "gallery":

                async def _do_gallery() -> list[Path]:
                    return await download_gallery(url, work_dir)

                gallery_files = await run_media_job(settings, _do_gallery, stage="download_gallery")
                if not gallery_files:
                    raise ConversionError("هیچ عکسی برای دانلود یافت نشد.")

                file_size = sum(f.stat().st_size for f in gallery_files)
                # Send up to 10 photos as media group
                media_group: list[Any] = [
                    InputMediaPhoto(media=FSInputFile(p)) for p in gallery_files[:10]
                ]
                await bot.send_media_group(
                    chat_id=chat_id,
                    media=media_group,
                    reply_to_message_id=reply_to_id,
                )

            else:
                return

            # Clean up status message
            try:
                if isinstance(status_msg, Message):
                    await status_msg.delete()
            except Exception:
                pass

            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            await ConversionRepo(session).log_conversion(
                telegram_id=user_id,
                source_kind="url",
                target_id=f"dl_{platform_key}",
                in_bytes=file_size,
                out_bytes=file_size,
                duration_ms=elapsed_ms,
                status="success",
            )
            log.info("downloader_success", platform=platform_key, user_id=user_id, size=file_size)

        except MewviaError as exc:
            err = error_text(exc.code)
            if isinstance(status_msg, Message):
                await status_msg.edit_text(err)
            else:
                await msg.answer(err)
        except Exception as exc:
            log.error("downloader_error", url=url, error=str(exc))
            err = "❌ خطا در دانلود رسانه. ممکن است لینک منقضی یا نامعتبر باشد."
            if isinstance(status_msg, Message):
                await status_msg.edit_text(err)
            else:
                await msg.answer(err)


feature = register(
    Feature(
        name="downloader",
        router=router,
        order=90,
    )
)
