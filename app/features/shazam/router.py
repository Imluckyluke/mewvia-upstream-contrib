"""Shazam music identification router."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from aiogram import Bot, F, Router, html
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks.shazam import ShazamCB
from app.config import Settings
from app.core.errors import ConversionError, MewviaError
from app.core.logging import get_logger
from app.core.quota import check_daily_quota
from app.core.units import human_bytes
from app.db.repo import ConversionRepo
from app.features.downloader.action_keyboards import build_audio_actions_kb
from app.features.registry import Feature, register
from app.media.downloader import download_spotify_track
from app.media.jobs import run_media_job
from app.media.kinds import resolve_media_message
from app.media.lyrics import build_full_lyrics_message, embed_lyrics_to_mp3, fetch_lyrics
from app.media.shazam import (
    ShazamTrack,
    build_shazam_keyboard,
    create_shazam_session_id,
    format_shazam_caption,
    get_shazam_session,
    identify_music_from_file,
    store_shazam_session,
)
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.progress import ProgressController
from app.ui.texts import t

log = get_logger(__name__)

router = Router(name="shazam")


async def ready_registry() -> EmojiRegistry:
    try:
        registry = get_emoji_registry()
        await registry.refresh_premium_flag()
        await registry.load()
        return registry
    except Exception:
        return EmojiRegistry()


async def execute_shazam_flow(
    msg: Message,
    file_id: str,
    reply_to_id: int | None,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Download Telegram media, extract audio, run Shazam fingerprinting, and present card."""
    chat_id = msg.chat.id
    registry = await ready_registry()

    progress: ProgressController | Message | None = None
    try:
        progress = await ProgressController.start(
            bot=bot,
            chat_id=chat_id,
            reply_to_id=reply_to_id or msg.message_id,
            initial_stage="در حال شناسایی اثر انگشت صوتی در Shazam…",
            session=session,
            replace_msg=msg if msg.text is not None else None,
        )
    except Exception as exc:
        log.warning("shazam_progress_start_failed", error=str(exc))
        progress = await msg.reply("⏳ در حال شناسایی اثر انگشت صوتی در Shazam…")

    status_msg = progress
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir:
        work_dir = Path(tmp_dir)
        src_file = work_dir / "input_media"

        try:
            await bot.download(file_id, destination=src_file)

            track: ShazamTrack | None = await identify_music_from_file(src_file, work_dir)

            if not track:
                err_text = (
                    f"{registry.text('warning')} <b>متأسفانه آهنگی شناسایی نشد.</b>\n\n"
                    "لطفاً مطمئن شوید بخش انتخابی دارای موزیک واضح با صدای خواننده یا ریتم مشخص است "
                    "و نویز پس‌زمینه زیاد نیست."
                )
                if status_msg is not None:
                    await status_msg.edit_text(err_text)
                return

            sid = create_shazam_session_id()
            await store_shazam_session(sid, track)

            caption = format_shazam_caption(track, registry, settings.bot_username)
            kb = build_shazam_keyboard(sid, track, registry)

            # Clean up status indicator
            if status_msg is not None:
                await status_msg.delete()

            # If cover URL exists, send as photo card for gorgeous presentation
            if track.cover_url:
                try:
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=track.cover_url,
                        caption=caption,
                        reply_markup=kb.as_markup(),
                        reply_to_message_id=reply_to_id,
                    )
                    return
                except Exception as p_exc:
                    log.debug("send_shazam_cover_photo_failed", error=str(p_exc))

            # Fallback to rich text card
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                reply_markup=kb.as_markup(),
                reply_to_message_id=reply_to_id,
            )

        except ConversionError as conv_err:
            if status_msg is not None:
                await status_msg.edit_text(f"❌ {conv_err}")
        except MewviaError as m_err:
            if status_msg is not None:
                await status_msg.edit_text(f"❌ {m_err}")
        except Exception as exc:
            log.error("shazam_flow_failed", error=str(exc))
            if status_msg is not None:
                await status_msg.edit_text("❌ متأسفانه در شناسایی موزیک مشکلی رخ داد.")


@router.message(Command("shazam", "findsong", "songid"))
async def cmd_shazam(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Identify music from a replied-to message or directly attached media."""
    resolved = resolve_media_message(message)
    if not resolved:
        await message.reply(
            "<b>🔍 شناسایی آهنگ با Shazam</b>\n\n"
            "روی یک <b>ویس، موزیک، ویدیو یا ویدیومسیج</b> ریپلای کن و بنویس:\n"
            "<code>/shazam</code>\n\n"
            "یا فایل صوتی/تصویری را بفرست و از منوی باز شده گزینه "
            "«🔍 این چه آهنگیه؟ (Shazam)» را بزن! 🐾"
        )
        return

    (_kind, _media_obj, file_id, file_size), target_msg = resolved

    if file_size > settings.download_limit:
        limit_str = human_bytes(settings.download_limit)
        await message.reply(f"❌ حجم فایل بیشتر از سقف مجاز ({limit_str}) است.")
        return

    reply_to_id = target_msg.message_id if target_msg else message.message_id
    await execute_shazam_flow(message, file_id, reply_to_id, bot, session, settings)


@router.callback_query(ShazamCB.filter(F.action == "close"))
async def close_shazam_card(query: CallbackQuery) -> None:
    if isinstance(query.message, Message):
        try:
            await query.message.delete()
        except Exception:
            pass
    await query.answer()


@router.callback_query(ShazamCB.filter(F.action == "lyrics"))
async def view_shazam_lyrics(query: CallbackQuery) -> None:
    await query.answer()
    cb = ShazamCB.unpack(query.data or "")
    track = await get_shazam_session(cb.sid)
    if not track:
        if isinstance(query.message, Message):
            await query.message.reply("اطلاعات این آهنگ منقضی شده است.")
        return

    lyrics = await fetch_lyrics(track.title, track.artist)
    if not lyrics:
        if isinstance(query.message, Message):
            await query.message.reply("متأسفانه متن ترانه‌ای برای این آهنگ یافت نشد.")
        return

    registry = await ready_registry()
    lyrics_msg = build_full_lyrics_message(track.title, track.artist, lyrics, registry)
    if isinstance(query.message, Message):
        await query.message.reply(lyrics_msg)


@router.callback_query(ShazamCB.filter(F.action == "dl"))
async def download_shazam_track(
    query: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Download the identified track as MP3 320 with full tags, cover, and lyrics."""
    cb = ShazamCB.unpack(query.data or "")
    track = await get_shazam_session(cb.sid)
    if not track or not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    user_id = query.from_user.id if query.from_user else 0
    if not await check_daily_quota(session, user_id, query):
        return

    await query.answer()

    chat_id = query.message.chat.id
    reply_to_id = (
        query.message.reply_to_message.message_id
        if query.message.reply_to_message
        else query.message.message_id
    )

    progress: ProgressController | Message | None = None
    try:
        progress = await ProgressController.start(
            bot=bot,
            chat_id=chat_id,
            reply_to_id=reply_to_id,
            initial_stage=f"در حال دانلود آهنگ «{track.title}»…",
            session=session,
        )
    except Exception as exc:
        log.warning("shazam_download_progress_start_failed", error=str(exc))
        progress = await query.message.reply(
            f"⏳ <b>در حال دانلود آهنگ «{html.quote(track.title)}»…</b>"
        )

    status_msg = progress
    start_time = time.perf_counter()
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir:
        work_dir = Path(tmp_dir)

        try:

            async def _do_dl() -> Path:
                return await download_spotify_track(
                    title=track.title,
                    artist=track.artist,
                    thumbnail_url=track.cover_url,
                    work_dir=work_dir,
                    max_bytes=settings.upload_limit,
                )

            downloaded_file = await run_media_job(settings, _do_dl, stage="shazam_download_track")
            file_size = downloaded_file.stat().st_size

            registry = await ready_registry()

            # Try embedding lyrics
            lyrics_obj = await fetch_lyrics(track.title, track.artist)
            if lyrics_obj:
                embed_lyrics_to_mp3(downloaded_file, lyrics_obj)

            caption = (
                f"{registry.text('music')} <b>{html.quote(track.title)}</b>\n"
                f"{registry.text('artist')} <b>خواننده:</b> {html.quote(track.artist)}\n"
            )
            if track.album:
                caption += f"{registry.text('album')} <b>آلبوم:</b> {html.quote(track.album)}\n"

            caption += f"\nدانلود شده با @{settings.bot_username} {registry.text('paw')}"

            # Actions keyboard (voice convert, speed change, tag edit)
            actions_kb = build_audio_actions_kb(registry)

            sent_audio = await bot.send_audio(
                chat_id=chat_id,
                audio=FSInputFile(downloaded_file),
                caption=caption[:1024],
                title=track.title[:64],
                performer=track.artist[:64],
                reply_markup=actions_kb.as_markup() if actions_kb else None,
                reply_to_message_id=reply_to_id,
            )

            # Send expandable lyrics if available
            if lyrics_obj and sent_audio:
                lyrics_card = build_full_lyrics_message(
                    track.title, track.artist, lyrics_obj, registry
                )
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=lyrics_card,
                        reply_to_message_id=sent_audio.message_id,
                    )
                except Exception as l_exc:
                    log.debug("shazam_lyrics_send_failed", error=str(l_exc))

            if status_msg is not None:
                await status_msg.delete()

            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            await ConversionRepo(session).log_conversion(
                telegram_id=user_id,
                source_kind="shazam",
                target_id="shazam_dl",
                in_bytes=file_size,
                out_bytes=file_size,
                duration_ms=elapsed_ms,
                status="success",
            )

        except Exception as exc:
            log.error("shazam_download_failed", error=str(exc))
            if status_msg is not None:
                await status_msg.edit_text("❌ متأسفانه دریافت فایل صوتی این آهنگ با خطا مواجه شد.")


feature = register(
    Feature(
        name="shazam",
        router=router,
        order=93,
    )
)
