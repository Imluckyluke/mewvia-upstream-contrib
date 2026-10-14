"""Direct music search router for Spotify and SoundCloud."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from aiogram import Bot, F, Router, html
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import MenuCB, MusicSearchCB
from app.config import Settings
from app.core.errors import MewviaError
from app.core.logging import get_logger
from app.core.quota import check_daily_quota
from app.core.units import fa_digits, human_bytes
from app.db.repo import ConversionRepo
from app.features.registry import Feature, register
from app.media.downloader import download_audio, download_spotify_track
from app.media.jobs import run_media_job
from app.media.lyrics import (
    build_full_lyrics_message,
    build_lyrics_caption_block,
    embed_lyrics_to_mp3,
    fetch_lyrics,
)
from app.media.music_search import (
    SearchTrack,
    create_search_session_id,
    get_search_session,
    search_soundcloud,
    search_spotify,
    store_search_session,
)
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.styles import Style
from app.ui.texts import error_text, t

log = get_logger(__name__)

router = Router(name="music_search")


class MusicSearchSG(StatesGroup):
    waiting_for_query = State()


async def ready_registry() -> EmojiRegistry:
    try:
        registry = get_emoji_registry()
        await registry.refresh_premium_flag()
        await registry.load()
        return registry
    except Exception:
        return EmojiRegistry()


def _build_search_kb(
    sid: str,
    platform: str,
    tracks: list[SearchTrack],
    registry: EmojiRegistry,
) -> KB:
    kb = KB(registry)

    # Row 1: Platform switchers
    sp_active = platform == "spotify"
    sc_active = platform == "soundcloud"

    sp_label = "اسپاتیفای (فعال)" if sp_active else "اسپاتیفای"
    sc_label = "ساندکلاد (فعال)" if sc_active else "ساندکلاد"

    kb.row(
        inline_button(
            sp_label,
            callback_data=MusicSearchCB(action="switch", platform="spotify", sid=sid).pack(),
            style=Style.CONFIRM if sp_active else Style.PRIMARY,
            emoji_slot="spotify",
            registry=registry,
        ),
        inline_button(
            sc_label,
            callback_data=MusicSearchCB(action="switch", platform="soundcloud", sid=sid).pack(),
            style=Style.CONFIRM if sc_active else Style.PRIMARY,
            emoji_slot="soundcloud",
            registry=registry,
        ),
    )

    # Row 2..N: Track download buttons
    for idx, t_item in enumerate(tracks[:5], 1):
        clean_title = t_item.title[:28] + ("…" if len(t_item.title) > 28 else "")
        btn_text = f"[{idx}] {clean_title} ({t_item.duration_str})"
        kb.row(
            inline_button(
                btn_text,
                callback_data=MusicSearchCB(
                    action="select", platform=platform, idx=idx, sid=sid
                ).pack(),
                style=Style.PRIMARY,
                emoji_slot="download",
                registry=registry,
            )
        )

    # Action footer
    kb.row(
        inline_button(
            "جستجوی جدید",
            callback_data=MusicSearchCB(action="prompt").pack(),
            style=Style.CONFIRM,
            emoji_slot="search",
            registry=registry,
        ),
        inline_button(
            "بستن",
            callback_data=MusicSearchCB(action="cancel").pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        ),
    )
    return kb


def _render_search_text(
    query_str: str,
    platform: str,
    tracks: list[SearchTrack],
    registry: EmojiRegistry,
) -> str:
    p_icon = registry.text("spotify") if platform == "spotify" else registry.text("soundcloud")
    p_name = (
        f"{p_icon} اسپاتیفای (Spotify)"
        if platform == "spotify"
        else f"{p_icon} ساندکلاد (SoundCloud)"
    )

    lines = [
        f"{registry.text('search')} <b>نتایج جستجو برای:</b> «<i>{html.quote(query_str)}</i>»\n"
        f"<b>منبع فعال:</b> {p_name}\n"
    ]

    if not tracks:
        lines.append(
            f"{registry.text('warning')} "
            "<i>هیچ نتیجه‌ای در این پلتفرم یافت نشد. می‌توانید منبع را عوض کنید.</i>"
        )
    else:
        for idx, tr in enumerate(tracks[:5], 1):
            dur_line = f"{registry.text('clock')} <code>{tr.duration_str}</code>"
            lines.append(
                f"[{idx}] <b>{html.quote(tr.title)}</b>\n"
                f"   {registry.text('user')} {html.quote(tr.artist)} | {dur_line}"
            )

    lines.append("\nبرای دریافت هر آهنگ، دکمه مربوط به آن را در زیر لمس کنید:")
    return "\n".join(lines)


@router.message(Command("music", "song", "search"))
async def cmd_music_search(message: Message, command: CommandObject, state: FSMContext) -> None:
    query = (command.args or "").strip()
    if not query:
        await state.set_state(MusicSearchSG.waiting_for_query)
        registry = await ready_registry()
        kb = (
            KB(registry)
            .button(
                t("common.cancel"),
                callback_data=MusicSearchCB(action="cancel").pack(),
                style=Style.DESTRUCTIVE,
                emoji_slot="error",
            )
            .as_markup()
        )
        await message.reply(
            "<b>🔍 جستجوی مستقیم موزیک (اسپاتیفای و ساندکلاد)</b>\n\n"
            "نام آهنگ، خواننده یا بخشی از متن آن را بفرست:\n\n"
            "<i>مثال:</i> <code>Shayea Ye Moghehaei</code> یا <code>The Weeknd Starboy</code>",
            reply_markup=kb,
        )
        return

    await _execute_search(message, query, platform="spotify", state=state)


@router.callback_query(MusicSearchCB.filter(F.action == "prompt"))
@router.callback_query(F.data == "msearch:prompt")
async def prompt_music_search(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MusicSearchSG.waiting_for_query)
    registry = await ready_registry()
    kb = (
        KB(registry)
        .button(
            t("common.cancel"),
            callback_data=MusicSearchCB(action="cancel").pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
        )
        .as_markup()
    )
    prompt_text = (
        "<b>🔍 جستجوی مستقیم موزیک</b>\n\n"
        "نام آهنگ یا خواننده مورد نظرت رو بفرست:\n"
        "<i>مثال:</i> <code>محسن چاوشی</code> یا <code>Blinding Lights</code>"
    )
    if isinstance(query.message, Message):
        await query.message.edit_text(prompt_text, reply_markup=kb)
    else:
        await query.answer()
        return
    await query.answer()


@router.message(MusicSearchSG.waiting_for_query, F.text)
async def process_search_query_input(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.reply("لطفاً یک عنوان معتبر بفرستید.")
        return
    await state.clear()
    await _execute_search(message, query, platform="spotify", state=state)


async def _execute_search(
    message: Message, query_str: str, platform: str, state: FSMContext
) -> None:
    status = await message.reply(f"⏳ <b>در حال جستجوی «{html.quote(query_str)}»…</b>")
    status_msg: Message | None = status if isinstance(status, Message) else None

    # Search Spotify first by default
    try:
        tracks = await search_spotify(query_str, limit=5)
    except Exception as exc:
        log.warning("spotify_search_failed", query=query_str, error=str(exc))
        tracks = []

    # If Spotify has no results, immediately fallback to SoundCloud
    active_platform = platform
    if not tracks:
        try:
            sc_tracks = await search_soundcloud(query_str, limit=5)
            if sc_tracks:
                tracks = sc_tracks
                active_platform = "soundcloud"
        except Exception:
            pass

    sid = create_search_session_id()
    session_data = {
        "query": query_str,
        "platform": active_platform,
        "spotify_results": [
            t_item.to_dict() for t_item in (tracks if active_platform == "spotify" else [])
        ],
        "soundcloud_results": [
            t_item.to_dict() for t_item in (tracks if active_platform == "soundcloud" else [])
        ],
    }
    await store_search_session(sid, session_data)

    registry = await ready_registry()
    kb = _build_search_kb(sid, active_platform, tracks, registry)
    card_text = _render_search_text(query_str, active_platform, tracks, registry)

    if status_msg:
        await status_msg.edit_text(card_text, reply_markup=kb.as_markup())
    else:
        await message.answer(card_text, reply_markup=kb.as_markup())


@router.callback_query(MusicSearchCB.filter(F.action == "switch"))
async def switch_music_platform(query: CallbackQuery, callback_data: MusicSearchCB) -> None:
    sid = callback_data.sid
    target_platform = callback_data.platform

    session_data = await get_search_session(sid)
    if not session_data or not isinstance(query.message, Message):
        await query.answer("جلسه جستجو منقضی شده است. مجدداً جستجو کنید.", show_alert=True)
        return

    query_str = str(session_data.get("query") or "")
    cached_key = f"{target_platform}_results"
    raw_cached = session_data.get(cached_key) or []

    if raw_cached:
        tracks = [SearchTrack.from_dict(d) for d in raw_cached]
    else:
        await query.answer("در حال دریافت نتایج…")
        if target_platform == "soundcloud":
            tracks = await search_soundcloud(query_str, limit=5)
        else:
            tracks = await search_spotify(query_str, limit=5)

        session_data[cached_key] = [t_item.to_dict() for t_item in tracks]
        session_data["platform"] = target_platform
        await store_search_session(sid, session_data)

    registry = await ready_registry()
    kb = _build_search_kb(sid, target_platform, tracks, registry)
    card_text = _render_search_text(query_str, target_platform, tracks, registry)

    try:
        await query.message.edit_text(card_text, reply_markup=kb.as_markup())
    except Exception:
        pass
    await query.answer()


@router.callback_query(MusicSearchCB.filter(F.action == "cancel"))
async def cancel_music_search(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text("جستجوی موزیک بسته شد.")
        except Exception:
            pass
    await query.answer()


@router.callback_query(MusicSearchCB.filter(F.action == "select"))
async def select_and_download_track(
    query: CallbackQuery,
    callback_data: MusicSearchCB,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    sid = callback_data.sid
    platform = callback_data.platform
    idx = callback_data.idx

    session_data = await get_search_session(sid)
    if not session_data or not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    raw_items = session_data.get(f"{platform}_results") or []
    if not (1 <= idx <= len(raw_items)):
        await query.answer("آیتم انتخاب شده معتبر نیست.", show_alert=True)
        return

    track_dict = raw_items[idx - 1]
    track = SearchTrack.from_dict(track_dict)
    user_id = query.from_user.id if query.from_user else 0

    if not await check_daily_quota(session, user_id, query):
        return

    await query.answer()
    p_name = "اسپاتیفای" if platform == "spotify" else "ساندکلاد"
    status_text = (
        f"⏳ <b>در حال دانلود آهنگ «{html.quote(track.title)}» از {p_name}…</b>\n"
        "لطفاً چند ثانیه شکیبا باشید 🐾"
    )
    status_msg = await query.message.reply(status_text)

    start_time = time.perf_counter()
    reply_to_id = (
        query.message.reply_to_message.message_id if query.message.reply_to_message else None
    )

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir:
        work_dir = Path(tmp_dir)

        try:
            if platform == "spotify":
                uploader = track.artist or "Spotify"

                async def _do_spotify() -> Path:
                    return await download_spotify_track(
                        title=track.title,
                        artist=uploader,
                        thumbnail_url=track.thumbnail_url,
                        work_dir=work_dir,
                        max_bytes=settings.upload_limit,
                    )

                downloaded_file = await run_media_job(
                    settings, _do_spotify, stage="download_spotify_track"
                )
            else:

                async def _do_soundcloud() -> Path:
                    return await download_audio(
                        track.url,
                        work_dir,
                        max_bytes=settings.upload_limit,
                    )

                downloaded_file = await run_media_job(
                    settings, _do_soundcloud, stage="download_soundcloud_track"
                )

            file_size = downloaded_file.stat().st_size
            if file_size > settings.upload_limit:
                limit_str = human_bytes(settings.upload_limit)
                size_str = human_bytes(file_size)
                await status_msg.edit_text(
                    f"❌ حجم فایل صوتی ({size_str}) بیشتر از سقف مجاز ({limit_str}) است."
                )
                return

            registry = await ready_registry()
            p_slot = "spotify" if platform == "spotify" else "soundcloud"
            dur_val = fa_digits(track.duration_str)

            # Check and embed song lyrics if available
            lyrics = await fetch_lyrics(
                track.title,
                track.artist,
                duration_seconds=track.duration_seconds,
            )
            if lyrics:
                embed_lyrics_to_mp3(downloaded_file, lyrics)

            lyrics_block, is_truncated = (
                build_lyrics_caption_block(lyrics, registry) if lyrics else ("", False)
            )

            caption = (
                f"{registry.text(p_slot)} <b>{html.quote(track.title)}</b>\n"
                f"{registry.text('user')} <b>خواننده:</b> {html.quote(track.artist)}\n"
                f"{registry.text('clock')} <b>مدت زمان:</b> <code>{dur_val}</code>"
                f"{lyrics_block}\n\n"
                f"دانلود شده با @{settings.bot_username} {registry.text('paw')}"
            )

            sent_audio = await bot.send_audio(
                chat_id=query.message.chat.id,
                audio=FSInputFile(downloaded_file),
                title=track.title[:64],
                performer=track.artist[:64],
                caption=caption[:1024],
                reply_to_message_id=reply_to_id,
            )

            if is_truncated and lyrics and sent_audio:
                full_lyrics_msg = build_full_lyrics_message(
                    track.title, track.artist, lyrics, registry
                )
                try:
                    await bot.send_message(
                        chat_id=query.message.chat.id,
                        text=full_lyrics_msg,
                        reply_to_message_id=sent_audio.message_id,
                    )
                except Exception as l_exc:
                    log.warning("send_full_lyrics_failed", error=str(l_exc))

            try:
                await status_msg.delete()
            except Exception:
                pass

            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            target_metric = "dl_spotify" if platform == "spotify" else "dl_soundcloud"
            await ConversionRepo(session).log_conversion(
                telegram_id=user_id,
                source_kind="url",
                target_id=target_metric,
                in_bytes=file_size,
                out_bytes=file_size,
                duration_ms=elapsed_ms,
                status="success",
            )
            log.info(
                "music_search_download_success",
                platform=platform,
                user_id=user_id,
                title=track.title,
            )

        except MewviaError as exc:
            err = str(exc) if str(exc) and str(exc) != exc.code else error_text(exc.code)
            try:
                await status_msg.edit_text(err)
            except Exception:
                pass
        except Exception as exc:
            log.error("music_search_download_failed", error=str(exc))
            try:
                await status_msg.edit_text("❌ خطا در دانلود موزیک. لطفاً دوباره تلاش کنید.")
            except Exception:
                pass


@router.callback_query(MenuCB.filter(F.target == "music_search"))
async def menu_music_search(query: CallbackQuery, state: FSMContext) -> None:
    await prompt_music_search(query, state)


feature = register(
    Feature(
        name="music_search",
        router=router,
        menu_label_key="menu.music_search",
        menu_emoji_slot="search",
        menu_style=Style.PRIMARY,
        order=92,
    )
)
