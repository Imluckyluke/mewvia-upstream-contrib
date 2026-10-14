"""Social media & web media downloader router.

Handles links sent by users:
1. Detects URL from message (YouTube, Instagram, TikTok, Twitter/X, Pinterest, SoundCloud, etc.)
2. Probes metadata and format availability
3. For YouTube videos and all Playlists/Albums: presents interactive options (quality/format/ZIP)
4. For all other platforms: auto-downloads immediately at top speed
5. Downloads within temporary sandbox under process-wide concurrency & quota controls
6. Sends media back to the user with custom captions and thumbnails
"""

from __future__ import annotations

import asyncio
import io
import re
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router, html
from aiogram.filters import BaseFilter, Command, CommandObject, StateFilter
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks.downloader import DownloaderCB
from app.config import Settings
from app.core.errors import ConversionError, MewviaError
from app.core.logging import get_logger
from app.core.quota import check_daily_quota
from app.core.units import fa_digits, human_bytes
from app.db.repo import ConversionRepo, InstagramCookieRepo
from app.features.registry import Feature, register
from app.filters import IsAdmin
from app.media.downloader import (
    create_token,
    detect_platform,
    download_audio,
    download_gallery,
    download_spotify_track,
    download_video,
    extract_first_url,
    get_download_context,
    probe_media_url,
    store_download_context,
)
from app.media.jobs import run_media_job
from app.media.lyrics import (
    build_full_lyrics_message,
    build_lyrics_caption_block,
    embed_lyrics_to_mp3,
    fetch_lyrics,
)
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.styles import Style
from app.ui.texts import error_text, t

log = get_logger(__name__)

router = Router(name="downloader")


def _add_to_zip(zip_path: Path, file_path: Path, arcname: str) -> None:
    with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(file_path, arcname=arcname)


async def ready_registry() -> EmojiRegistry:
    try:
        registry = get_emoji_registry()
        await registry.refresh_premium_flag()
        await registry.load()
        return registry
    except Exception:
        return get_emoji_registry()


async def _perform_download(
    *,
    chat_id: int,
    user_id: int,
    reply_to_id: int | None,
    action: str,
    ctx: dict[str, Any],
    status_msg: Message | None,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    fallback_msg: Message | None = None,
) -> None:
    """Execute the media download and send the media back to the user."""
    registry = await ready_registry()
    url = str(ctx.get("url", ""))
    platform_key = str(ctx.get("platform_key", "other"))
    title = str(ctx.get("title", "media"))
    start_time = time.perf_counter()

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir:
        work_dir = Path(tmp_dir)

        cookie_file_str: str | None = None
        cookie_record = None
        repo_cookie = InstagramCookieRepo(session)
        if platform_key == "instagram":
            cookie_record = await repo_cookie.get_next_cookie()
            if cookie_record and cookie_record.cookie_text:
                ig_cookie_path = work_dir / "ig_session.txt"
                ig_cookie_path.write_text(cookie_record.cookie_text, encoding="utf-8")
                cookie_file_str = str(ig_cookie_path)

        try:
            if action.startswith("video"):
                max_h: int | None = None
                if action == "video_720":
                    max_h = 720
                elif action == "video_480":
                    max_h = 480
                elif action == "video_360":
                    max_h = 360

                async def _do_video() -> Path:
                    return await download_video(
                        url,
                        work_dir,
                        max_height=max_h,
                        max_bytes=settings.upload_limit,
                        cookie_file=cookie_file_str,
                    )

                downloaded_file = await run_media_job(settings, _do_video, stage="download_video")
                file_size = downloaded_file.stat().st_size

                if file_size > settings.upload_limit:
                    limit_str = human_bytes(settings.upload_limit)
                    size_str = human_bytes(file_size)
                    err_msg = f"❌ حجم ویدیو ({size_str}) بیشتر از سقف مجاز ({limit_str}) است."
                    if status_msg:
                        await status_msg.edit_text(err_msg)
                    elif fallback_msg:
                        await fallback_msg.reply(err_msg)
                    else:
                        await bot.send_message(chat_id=chat_id, text=err_msg)
                    return

                caption = (
                    f"{registry.text('video')} <b>{html.quote(title)}</b>\n\n"
                    f"دانلود شده با @{settings.bot_username} {registry.text('paw')}"
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
                        cookie_file=cookie_file_str,
                    )

                downloaded_file = await run_media_job(settings, _do_audio, stage="download_audio")
                file_size = downloaded_file.stat().st_size

                if file_size > settings.upload_limit:
                    limit_str = human_bytes(settings.upload_limit)
                    size_str = human_bytes(file_size)
                    err_msg = (
                        f"{registry.text('error')} حجم فایل صوتی ({size_str}) "
                        f"بیشتر از سقف مجاز ({limit_str}) است."
                    )
                    if status_msg:
                        await status_msg.edit_text(err_msg)
                    elif fallback_msg:
                        await fallback_msg.reply(err_msg)
                    else:
                        await bot.send_message(chat_id=chat_id, text=err_msg)
                    return

                uploader = ctx.get("uploader") or "Mewvia"
                dur_secs = ctx.get("duration_seconds")
                dur_int = (
                    int(dur_secs) if isinstance(dur_secs, (int, float)) and dur_secs > 0 else None
                )

                # Fetch and embed lyrics if available
                lyrics = await fetch_lyrics(title, str(uploader), duration_seconds=dur_int)
                if lyrics:
                    embed_lyrics_to_mp3(downloaded_file, lyrics)

                lyrics_block, is_truncated = (
                    build_lyrics_caption_block(lyrics, registry) if lyrics else ("", False)
                )

                caption = (
                    f"{registry.text('audio')} <b>{html.quote(title)}</b>\n"
                    f"{registry.text('user')} <b>خواننده / سازنده:</b> {html.quote(str(uploader))}"
                    f"{lyrics_block}\n\n"
                    f"دانلود شده با @{settings.bot_username} {registry.text('paw')}"
                )
                sent_audio = await bot.send_audio(
                    chat_id=chat_id,
                    audio=FSInputFile(downloaded_file),
                    title=title[:64],
                    performer=str(uploader)[:64],
                    caption=caption[:1024],
                    reply_to_message_id=reply_to_id,
                )

                if is_truncated and lyrics and sent_audio:
                    full_lyrics_msg = build_full_lyrics_message(
                        title, str(uploader), lyrics, registry
                    )
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=full_lyrics_msg,
                            reply_to_message_id=sent_audio.message_id,
                        )
                    except Exception as l_exc:
                        log.warning("send_full_lyrics_failed", error=str(l_exc))

            elif action == "gallery":

                async def _do_gallery() -> list[Path]:
                    return await download_gallery(
                        url,
                        work_dir,
                        cookie_file=cookie_file_str,
                    )

                gallery_files = await run_media_job(settings, _do_gallery, stage="download_gallery")
                if not gallery_files:
                    raise ConversionError("هیچ عکسی برای دانلود یافت نشد.")

                file_size = sum(f.stat().st_size for f in gallery_files)
                gallery_caption = (
                    f"{registry.text('photo')} <b>{html.quote(title)}</b>\n\n"
                    f"دانلود شده با @{settings.bot_username} {registry.text('paw')}"
                )

                if len(gallery_files) == 1:
                    single_file = gallery_files[0]
                    if single_file.suffix.lower() == ".mp4":
                        await bot.send_video(
                            chat_id=chat_id,
                            video=FSInputFile(single_file),
                            caption=gallery_caption[:1024],
                            reply_to_message_id=reply_to_id,
                        )
                    else:
                        await bot.send_photo(
                            chat_id=chat_id,
                            photo=FSInputFile(single_file),
                            caption=gallery_caption[:1024],
                            reply_to_message_id=reply_to_id,
                        )
                else:
                    for chunk_idx in range(0, len(gallery_files), 10):
                        chunk = gallery_files[chunk_idx : chunk_idx + 10]
                        media_group: list[Any] = []
                        for i, p in enumerate(chunk):
                            cap = gallery_caption[:1024] if chunk_idx == 0 and i == 0 else None
                            if p.suffix.lower() == ".mp4":
                                media_group.append(
                                    InputMediaVideo(media=FSInputFile(p), caption=cap)
                                )
                            else:
                                media_group.append(
                                    InputMediaPhoto(media=FSInputFile(p), caption=cap)
                                )
                        if len(media_group) == 1:
                            single_item = chunk[0]
                            if single_item.suffix.lower() == ".mp4":
                                await bot.send_video(
                                    chat_id=chat_id,
                                    video=FSInputFile(single_item),
                                    reply_to_message_id=reply_to_id,
                                )
                            else:
                                await bot.send_photo(
                                    chat_id=chat_id,
                                    photo=FSInputFile(single_item),
                                    reply_to_message_id=reply_to_id,
                                )
                        else:
                            await bot.send_media_group(
                                chat_id=chat_id,
                                media=media_group,
                                reply_to_message_id=reply_to_id,
                            )
                        await asyncio.sleep(0.3)

            elif action == "spotify_track":
                uploader = ctx.get("uploader") or "Spotify"
                thumb_url = ctx.get("thumbnail_url")

                async def _do_spotify() -> Path:
                    return await download_spotify_track(
                        title=title,
                        artist=str(uploader),
                        thumbnail_url=thumb_url,
                        work_dir=work_dir,
                        max_bytes=settings.upload_limit,
                    )

                downloaded_file = await run_media_job(
                    settings, _do_spotify, stage="download_spotify"
                )
                file_size = downloaded_file.stat().st_size

                # Fetch and embed lyrics if available
                lyrics = await fetch_lyrics(title, str(uploader))
                if lyrics:
                    embed_lyrics_to_mp3(downloaded_file, lyrics)

                lyrics_block, is_truncated = (
                    build_lyrics_caption_block(lyrics, registry) if lyrics else ("", False)
                )

                caption = (
                    f"{registry.text('spotify')} <b>{html.quote(title)}</b>\n"
                    f"{registry.text('user')} <b>خواننده:</b> {html.quote(str(uploader))}"
                    f"{lyrics_block}\n\n"
                    f"دانلود شده با @{settings.bot_username} {registry.text('paw')}"
                )
                sent_audio = await bot.send_audio(
                    chat_id=chat_id,
                    audio=FSInputFile(downloaded_file),
                    title=title[:64],
                    performer=str(uploader)[:64],
                    caption=caption[:1024],
                    reply_to_message_id=reply_to_id,
                )

                if is_truncated and lyrics and sent_audio:
                    full_lyrics_msg = build_full_lyrics_message(
                        title, str(uploader), lyrics, registry
                    )
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=full_lyrics_msg,
                            reply_to_message_id=sent_audio.message_id,
                        )
                    except Exception as l_exc:
                        log.warning("send_full_lyrics_failed", error=str(l_exc))

            elif action in ("pl_zip", "pl_audio_all", "pl_audio_first5", "pl_video_all"):
                items = list(ctx.get("playlist_items") or [])
                if not items:
                    raise ConversionError("ترکی در این پلی‌لیست برای دانلود یافت نشد.")

                is_zip = action == "pl_zip"
                items_to_download = items[:5] if action == "pl_audio_first5" else items[:100]
                total_items = len(items_to_download)
                sent_count = 0
                total_bytes = 0

                tracks_dir = work_dir / "tracks"
                tracks_dir.mkdir(parents=True, exist_ok=True)
                clean_title = re.sub(r'[\\/*?:"<>|]', "", title)[:30].strip() or "album"
                zip_path = work_dir / f"{clean_title}.zip"

                # Concurrent worker semaphore (5 workers in parallel for speed)
                sem = asyncio.Semaphore(5)

                async def _worker(
                    idx: int, it: dict[str, Any]
                ) -> tuple[int, dict[str, Any], Path | None]:
                    # Stagger requests slightly to avoid triggering YouTube instant burst limits
                    await asyncio.sleep(0.15 * ((idx - 1) % 5))
                    it_title = str(it.get("title") or f"Track {idx}")
                    it_artist = str(it.get("artist") or it.get("uploader") or "")
                    it_url = str(it.get("url") or "")
                    track_dir = tracks_dir / f"t_{idx}"
                    track_dir.mkdir(parents=True, exist_ok=True)

                    async with sem:
                        try:
                            if platform_key == "spotify":
                                p = await download_spotify_track(
                                    title=it_title,
                                    artist=it_artist,
                                    thumbnail_url=ctx.get("thumbnail_url"),
                                    work_dir=track_dir,
                                    max_bytes=settings.upload_limit,
                                )
                                return idx, it, p
                            elif action == "pl_video_all":
                                if it_url:
                                    p = await download_video(
                                        it_url,
                                        track_dir,
                                        max_bytes=settings.upload_limit,
                                    )
                                    return idx, it, p
                            else:
                                if it_url:
                                    p = await download_audio(
                                        it_url,
                                        track_dir,
                                        max_bytes=settings.upload_limit,
                                    )
                                    return idx, it, p
                        except Exception as exc:
                            log.warning(
                                "parallel_track_dl_failed", idx=idx, title=it_title, error=str(exc)
                            )
                            return idx, it, None
                    return idx, it, None

                tasks = [
                    asyncio.create_task(_worker(idx, it))
                    for idx, it in enumerate(items_to_download, 1)
                ]

                last_edit_time = 0.0
                # Stream results as workers finish
                for fut in asyncio.as_completed(tasks):
                    idx, it, file_path = await fut
                    it_title = str(it.get("title") or f"Track {idx}")
                    it_artist = str(it.get("artist") or it.get("uploader") or "")

                    if file_path and file_path.is_file():
                        f_size = file_path.stat().st_size
                        total_bytes += f_size
                        sent_count += 1

                        if is_zip:
                            arcname = f"{idx:02d} - {it_artist} - {it_title}{file_path.suffix}"
                            await asyncio.to_thread(_add_to_zip, zip_path, file_path, arcname)
                        else:
                            try:
                                if action == "pl_video_all":
                                    caption_v = (
                                        f"{registry.text('video')} <b>{html.quote(it_title)}</b>"
                                    )
                                    await bot.send_video(
                                        chat_id=chat_id,
                                        video=FSInputFile(file_path),
                                        caption=caption_v,
                                        reply_to_message_id=reply_to_id,
                                    )
                                else:
                                    await bot.send_audio(
                                        chat_id=chat_id,
                                        audio=FSInputFile(file_path),
                                        title=it_title[:64],
                                        performer=it_artist[:64],
                                        reply_to_message_id=reply_to_id,
                                    )
                                await asyncio.sleep(0.4)
                            except Exception as send_exc:
                                log.warning(
                                    "track_send_failed", title=it_title, error=str(send_exc)
                                )

                    now = time.perf_counter()
                    if status_msg is not None and (
                        now - last_edit_time >= 1.5 or sent_count == total_items
                    ):
                        last_edit_time = now
                        cur_fa = fa_digits(str(sent_count))
                        tot_fa = fa_digits(str(total_items))
                        prog_text = (
                            f"{registry.text('processing')} "
                            f"<b>در حال پردازش آلبوم ({cur_fa} از {tot_fa})…</b>\n\n"
                            f"{registry.text('audio')} <b>{html.quote(it_title)}</b>"
                        )
                        try:
                            await status_msg.edit_text(prog_text)
                        except Exception:
                            pass

                if sent_count == 0:
                    if status_msg is not None:
                        err_pl_text = (
                            f"{registry.text('error')} "
                            "<b>امکان دریافت ترک‌های این پلی‌لیست وجود نداشت.</b>\n"
                            "ممکن است قطعات دارای محدودیت یا در این منطقه مسدود باشند."
                        )
                        await status_msg.edit_text(err_pl_text)
                        status_msg = None
                    return

                if is_zip and zip_path.is_file() and zip_path.stat().st_size > 0:
                    zip_size = zip_path.stat().st_size
                    total_bytes = zip_size
                    caption = (
                        f"{registry.text('zip')} <b>آلبوم کامل: {html.quote(title)}</b>\n\n"
                        f"{registry.text('bullet')} تعداد: "
                        f"<code>{fa_digits(str(sent_count))}</code> ترک\n"
                        f"{registry.text('file')} حجم: <code>{human_bytes(zip_size)}</code>\n\n"
                        f"دانلود شده با @{settings.bot_username} {registry.text('paw')}"
                    )
                    await bot.send_document(
                        chat_id=chat_id,
                        document=FSInputFile(zip_path, filename=f"{clean_title}.zip"),
                        caption=caption[:1024],
                        reply_to_message_id=reply_to_id,
                    )

                file_size = total_bytes
                if status_msg is not None:
                    done_text = (
                        f"{registry.text('ok')} <b>دانلود آلبوم / پلی‌لیست به پایان رسید!</b>\n\n"
                        f"{registry.text('pack')} "
                        f"<code>{fa_digits(str(sent_count))}</code> ترک با موفقیت آماده شد."
                    )
                    await status_msg.edit_text(done_text)
                    status_msg = None

            else:
                return

            if cookie_record:
                await repo_cookie.record_usage(cookie_record.id, success=True)

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
            if cookie_record:
                await repo_cookie.record_usage(cookie_record.id, success=False, error=str(exc))
            err = str(exc) if str(exc) and str(exc) != exc.code else error_text(exc.code)
            if isinstance(status_msg, Message):
                await status_msg.edit_text(err)
            elif fallback_msg:
                await fallback_msg.reply(err)
            else:
                await bot.send_message(chat_id=chat_id, text=err)
        except Exception as exc:
            if cookie_record:
                await repo_cookie.record_usage(cookie_record.id, success=False, error=str(exc))
            log.error("downloader_error", url=url, error=str(exc))
            err = "❌ خطا در دانلود رسانه. ممکن است لینک منقضی یا نامعتبر باشد."
            if isinstance(status_msg, Message):
                await status_msg.edit_text(err)
            elif fallback_msg:
                await fallback_msg.reply(err)
            else:
                await bot.send_message(chat_id=chat_id, text=err)


async def _handle_url_input(
    message: Message,
    url: str,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Analyze the given URL and present options for YouTube/Playlists or auto-download directly."""
    registry = await ready_registry()
    status = await message.reply("⏳ <b>در حال دریافت اطلاعات لینک…</b>")
    status_msg: Message | None = status if isinstance(status, Message) else None

    try:
        info = await probe_media_url(url)
    except MewviaError as exc:
        err = str(exc) if str(exc) and str(exc) != exc.code else error_text(exc.code)
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

    # User preference: Only YouTube videos and all Playlists/Albums show selection cards.
    # All other single media (Instagram, TikTok, Twitter, Spotify, etc.) auto-download at once!
    is_interactive = info.is_playlist or (info.platform_key == "youtube")

    if is_interactive:
        token = create_token()
        await store_download_context(
            token,
            {
                "url": url,
                "platform_key": info.platform_key,
                "platform_name": info.platform_name,
                "title": info.title,
                "uploader": info.uploader,
                "thumbnail_url": info.thumbnail_url,
                "duration_str": info.duration_str,
                "reply_to_id": message.message_id,
                "is_playlist": info.is_playlist,
                "playlist_count": info.playlist_count,
                "playlist_items": list(info.playlist_items),
            },
        )

        kb = KB(registry)

        if info.is_playlist:
            count_fa = fa_digits(str(info.playlist_count))
            kb.row(
                inline_button(
                    f"دانلود یکجا (فایل ZIP) [{count_fa} ترک]",
                    callback_data=DownloaderCB(action="pl_zip", token=token).pack(),
                    style=Style.CONFIRM,
                    emoji_slot="zip",
                    registry=registry,
                )
            )
            kb.row(
                inline_button(
                    "ارسال دونه‌دونه ترک‌ها (MP3)",
                    callback_data=DownloaderCB(action="pl_audio_all", token=token).pack(),
                    style=Style.PRIMARY,
                    emoji_slot="audio",
                    registry=registry,
                )
            )
            if info.platform_key != "spotify":
                kb.row(
                    inline_button(
                        f"دانلود تمام ویدیوها ({count_fa} عدد)",
                        callback_data=DownloaderCB(action="pl_video_all", token=token).pack(),
                        style=Style.NEUTRAL,
                        emoji_slot="video",
                        registry=registry,
                    )
                )
            if info.playlist_count > 5:
                kb.row(
                    inline_button(
                        "دانلود ۵ مورد اول (سریع)",
                        callback_data=DownloaderCB(action="pl_audio_first5", token=token).pack(),
                        style=Style.NEUTRAL,
                        emoji_slot="lightning",
                        registry=registry,
                    )
                )
        else:
            # YouTube single video
            kb.row(
                inline_button(
                    "بهترین کیفیت",
                    callback_data=DownloaderCB(action="video", token=token).pack(),
                    style=Style.PRIMARY,
                    emoji_slot="video",
                    registry=registry,
                ),
                inline_button(
                    "کیفیت 720p HD",
                    callback_data=DownloaderCB(action="video_720", token=token).pack(),
                    style=Style.PRIMARY,
                    emoji_slot="quality_hd",
                    registry=registry,
                ),
            )
            kb.row(
                inline_button(
                    "کیفیت 480p (حجم کم)",
                    callback_data=DownloaderCB(action="video_480", token=token).pack(),
                    style=Style.NEUTRAL,
                    emoji_slot="quality_sd",
                    registry=registry,
                ),
                inline_button(
                    "استخراج صدا (MP3)",
                    callback_data=DownloaderCB(action="audio", token=token).pack(),
                    style=Style.CONFIRM,
                    emoji_slot="audio",
                    registry=registry,
                ),
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

        uploader_line = (
            f"{registry.text('user')} <b>سازنده:</b> {html.quote(info.uploader)}\n"
            if info.uploader
            else ""
        )
        duration_line = (
            f"{registry.text('clock')} <b>مدت زمان:</b> "
            f"<code>{fa_digits(info.duration_str)}</code>\n"
            if info.duration_seconds
            else ""
        )

        if info.is_playlist:
            type_str = "پلی‌لیست / آلبوم"
            count_str = fa_digits(str(info.playlist_count))
            count_line = (
                f"{registry.text('bullet')} <b>تعداد ترک‌ها:</b> <code>{count_str}</code> عدد\n"
            )
            p_slot = (
                info.platform_key
                if info.platform_key in ("youtube", "spotify", "soundcloud", "instagram")
                else "download"
            )
            card_text = (
                f"{registry.text(p_slot)} <b>دانلود {type_str} از {info.platform_name}</b>\n\n"
                f"{registry.text('text')} <b>عنوان:</b> {html.quote(info.title)}\n"
                f"{uploader_line}"
                f"{count_line}\n"
                "نحوه دانلود را انتخاب کن:"
            )
        else:
            card_text = (
                f"{registry.text('youtube')} <b>دانلود از یوتیوب</b>\n\n"
                f"{registry.text('text')} <b>عنوان:</b> {html.quote(info.title)}\n"
                f"{uploader_line}"
                f"{duration_line}\n"
                "کیفیت یا فرمت مورد نظرت را انتخاب کن:"
            )

        if status_msg:
            await status_msg.edit_text(card_text, reply_markup=kb.as_markup())
        else:
            await message.answer(card_text, reply_markup=kb.as_markup())
        return

    # Direct auto-download without questioning for all other platforms
    user_id = message.from_user.id if message.from_user else 0
    if not await check_daily_quota(session, user_id, message):
        return

    if info.platform_key == "spotify":
        action = "spotify_track"
    elif info.is_gallery:
        action = "gallery"
    elif info.is_audio_only:
        action = "audio"
    else:
        action = "video"

    if status_msg:
        try:
            await status_msg.edit_text("⏳ <b>در حال دانلود رسانه از سرور…</b>")
        except Exception:
            pass

    ctx: dict[str, Any] = {
        "url": url,
        "platform_key": info.platform_key,
        "platform_name": info.platform_name,
        "title": info.title,
        "uploader": info.uploader,
        "thumbnail_url": info.thumbnail_url,
        "duration_str": info.duration_str,
        "reply_to_id": message.message_id,
        "is_playlist": False,
        "playlist_count": 0,
        "playlist_items": [],
    }

    await _perform_download(
        chat_id=message.chat.id,
        user_id=user_id,
        reply_to_id=message.message_id,
        action=action,
        ctx=ctx,
        status_msg=status_msg,
        bot=bot,
        session=session,
        settings=settings,
        fallback_msg=message,
    )


@router.message(Command("dl"))
async def cmd_download_url(
    message: Message,
    command: CommandObject,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Download media from command: `/dl <url>`."""
    arg_url = (command.args or "").strip()
    url = extract_first_url(arg_url)
    if not url:
        await message.reply(
            "لطفاً لینک مورد نظرت را به این شکل بفرست:\n"
            "<code>/dl https://instagram.com/reel/...</code>"
        )
        return
    await _handle_url_input(message, url, bot, session, settings)


@router.message(Command("cookie", "cookies"), IsAdmin())
async def cmd_cookie_info(message: Message, session: AsyncSession) -> None:
    """Show Instagram cookies management dashboard for admins."""
    from app.admin.cookies_router import ready_registry, render_cookies_dashboard

    registry = await ready_registry()
    text, kb = await render_cookies_dashboard(session, registry)
    await message.reply(text, reply_markup=kb.as_markup())


@router.message(
    F.chat.type == "private",
    IsAdmin(),
    F.document,
)
async def upload_cookie_file(message: Message, bot: Bot, session: AsyncSession) -> None:
    """Allow admins to upload cookies.txt directly to add to the account pool."""
    doc = message.document
    if not doc or not doc.file_name:
        return
    file_name = doc.file_name.lower()
    valid_keywords = ("cookie", "insta", "instagram", "youtube", "yt")
    if not (file_name.endswith(".txt") and any(k in file_name for k in valid_keywords)):
        return

    file_info = await bot.get_file(doc.file_id)
    if not file_info.file_path:
        await message.reply("❌ خطا در دریافت فایل از تلگرام.")
        return

    buf = io.BytesIO()
    await bot.download_file(file_info.file_path, destination=buf)
    raw_text = buf.getvalue().decode("utf-8", errors="ignore")

    from app.admin.cookies_router import (
        ready_registry,
        render_cookies_dashboard,
        sync_active_cookie_to_disk,
    )
    from app.media.cookie_checker import (
        check_instagram_cookie_health_sync,
        parse_and_normalize_cookies,
    )

    netscape_text, cookie_dict = parse_and_normalize_cookies(raw_text)
    account_id = cookie_dict.get("ds_user_id")
    repo = InstagramCookieRepo(session)
    count = await repo.count()
    label = f"اکانت {count + 1}" if not account_id else f"اکانت {account_id[:8]}"

    is_valid, _, err = await asyncio.to_thread(check_instagram_cookie_health_sync, cookie_dict)
    status_str = "valid" if is_valid else "invalid"

    cookie = await repo.add(
        label=label,
        cookie_text=netscape_text,
        account_id=account_id,
        status=status_str,
    )
    if not is_valid:
        cookie.error_message = err

    await sync_active_cookie_to_disk(session)
    await session.flush()

    registry = await ready_registry()
    _, kb = await render_cookies_dashboard(session, registry)
    status_badge = "🟢 سالم و فعال در چرخه" if is_valid else f"⚠️ نیاز به بررسی ({err})"
    await message.reply(
        f"🍪 <b>اکانت «{html.quote(label)}» با موفقیت اضافه شد!</b>\n\n"
        f"وضعیت اولیه: {status_badge}\n"
        "این کوکی در دیتابیس ثبت شد و به صورت خودکار برای دانلودهای اینستاگرام استفاده می‌شود.",
        reply_markup=kb.as_markup(),
    )


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
async def auto_detect_url_message(
    message: Message,
    extracted_url: str,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Automatically catch URLs sent as private chat messages in idle state."""
    await _handle_url_input(message, extracted_url, bot, session, settings)


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

    await _perform_download(
        chat_id=msg.chat.id,
        user_id=user_id,
        reply_to_id=ctx.get("reply_to_id"),
        action=action,
        ctx=ctx,
        status_msg=status_msg,
        bot=bot,
        session=session,
        settings=settings,
        fallback_msg=msg,
    )


feature = register(
    Feature(
        name="downloader",
        router=router,
        order=90,
    )
)
