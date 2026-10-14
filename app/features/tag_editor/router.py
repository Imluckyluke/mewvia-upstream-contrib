"""Music tag & cover editor router.

Flow (FSM-guided, same pattern as stickerpack):
1. User sends an audio file → panel gains a «🎵 ویرایش تگ و کاور» button.
2. Tapping it shows the tag card (read via mutagen, no re-encode).
3. Tap a field → bot asks for the new value (FSM state per field).
4. Cover → user sends a photo; bot validates, resizes to ≤1000px JPEG.
5. «✅ ذخیره و ارسال» applies all staged edits (mutagen in-place) and sends
   the retagged file back with updated title/performer.

State keys (all under one FSM group `TagEditFSM`):
- file_id / kind / file_size / reply_to_id: source media (from panel)
- tag_title / tag_artist / tag_album / tag_genre / tag_track: staged values
- cover_file_id: staged cover photo file_id
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from aiogram import Bot, F, Router, html
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message
from mutagen import File as MutagenFile
from PIL import Image as PILImage
from PIL.Image import Resampling
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import AudioToolCB
from app.config import Settings
from app.core.errors import ConversionError, MewviaError
from app.core.logging import get_logger
from app.core.units import fa_digits, fa_to_en_digits
from app.db.repo import ConversionRepo
from app.features.registry import Feature, register
from app.media.kinds import MediaKind, resolve_panel_media
from app.media.tag_editor import (
    MAX_ALBUM_LEN,
    MAX_ARTIST_LEN,
    MAX_GENRE_LEN,
    MAX_TITLE_LEN,
    MAX_TRACK,
    READABLE_SUFFIXES,
    WRITABLE_SUFFIXES,
    apply_tags,
    format_tags_card,
    read_tags,
)
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.styles import Style
from app.ui.texts import error_text, t

log = get_logger(__name__)

router = Router(name="tag_editor")


class TagEditFSM(StatesGroup):
    waiting_for_value = State()
    waiting_for_cover = State()


class TagFieldCB(CallbackData, prefix="tagf"):
    """Tag editor field selection + navigation."""

    action: str  # field, cover, save, cancel
    field: str = ""


FIELD_LABELS: dict[str, str] = {
    "title": "نام آهنگ",
    "artist": "خواننده",
    "album": "آلبوم",
    "track": "شماره ترک",
    "genre": "ژانر",
}

FIELD_ICONS: dict[str, str] = {
    "title": "📝",
    "artist": "🎤",
    "album": "💿",
    "track": "🔢",
    "genre": "🎼",
}

STAGED_KEY_BY_FIELD: dict[str, str] = {
    "title": "tag_title",
    "artist": "tag_artist",
    "album": "tag_album",
    "track": "tag_track",
    "genre": "tag_genre",
}

#: Normal icon slot per field; a staged field switches to the ``ok`` slot so
#: the button carries the owner's premium tick (or its ✅ fallback).
FIELD_SLOTS: dict[str, str] = {
    "title": "edit",
    "artist": "user",
    "album": "file",
    "track": "bullet",
    "genre": "audio",
}

FIELD_LIMITS: dict[str, int] = {
    "title": MAX_TITLE_LEN,
    "artist": MAX_ARTIST_LEN,
    "album": MAX_ALBUM_LEN,
    "genre": MAX_GENRE_LEN,
}


async def ready_registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


def tag_editor_kb(registry: EmojiRegistry, staged: dict[str, object] | None = None) -> KB:
    """Editor panel keyboard.

    Uses the standard ``emoji_slot`` mechanism like every other button in the
    bot: a staged field switches its icon slot to ``ok``, so the tick renders
    as the owner's premium emoji when set, or the ✅ fallback otherwise.
    """
    staged = staged or {}

    def slot(field: str) -> str:
        if staged.get(STAGED_KEY_BY_FIELD[field]) is not None:
            return "ok"
        return FIELD_SLOTS[field]

    def label(field: str) -> str:
        return f"{FIELD_ICONS[field]} {FIELD_LABELS[field]}"

    kb = KB(registry)
    kb.row(
        inline_button(
            label("title"),
            callback_data=TagFieldCB(action="field", field="title").pack(),
            style=Style.PRIMARY,
            emoji_slot=slot("title"),
            registry=registry,
        ),
        inline_button(
            label("artist"),
            callback_data=TagFieldCB(action="field", field="artist").pack(),
            style=Style.PRIMARY,
            emoji_slot=slot("artist"),
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            label("album"),
            callback_data=TagFieldCB(action="field", field="album").pack(),
            style=Style.PRIMARY,
            emoji_slot=slot("album"),
            registry=registry,
        ),
        inline_button(
            label("track"),
            callback_data=TagFieldCB(action="field", field="track").pack(),
            style=Style.PRIMARY,
            emoji_slot=slot("track"),
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            label("genre"),
            callback_data=TagFieldCB(action="field", field="genre").pack(),
            style=Style.PRIMARY,
            emoji_slot=slot("genre"),
            registry=registry,
        ),
        inline_button(
            "🖼️ کاور جدید",
            callback_data=TagFieldCB(action="cover").pack(),
            style=Style.CONFIRM,
            emoji_slot="ok" if staged.get("cover_file_id") else "photo",
            registry=registry,
        ),
    )

    kb.row(
        inline_button(
            "✅ ذخیره و ارسال",
            callback_data=TagFieldCB(action="save").pack(),
            style=Style.CONFIRM,
            emoji_slot="ok",
            registry=registry,
        ),
        inline_button(
            t("common.cancel"),
            callback_data=TagFieldCB(action="cancel").pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        ),
    )
    return kb


@router.callback_query(AudioToolCB.filter(F.action == "tag_menu"))
async def open_tag_editor(
    query: CallbackQuery,
    callback_data: AudioToolCB,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
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
    if kind not in (MediaKind.AUDIO, MediaKind.VOICE):
        await query.answer("ویرایش تگ فقط برای فایل صوتی و موزیک است.", show_alert=True)
        return

    await query.answer()
    status = await msg.edit_text("⏳ <b>در حال خواندن تگ‌های فایل…</b>", reply_markup=None)
    status_msg: Message | None = status if isinstance(status, Message) else None

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir:
        src = Path(tmp_dir) / "tag_src"
        try:
            await bot.download(file_id, destination=src)
        except Exception as exc:
            log.warning("tag_editor_download_failed", error=str(exc))
            if status_msg:
                await status_msg.edit_text(error_text("unknown"))
            return

        suffix = (src.suffix or "").lower()
        if suffix not in READABLE_SUFFIXES:
            # Telegram downloads usually lose the extension — sniff the
            # container via mutagen and rename so tag routing works.
            suffix = _sniff_audio_suffix(src)
            sniffed = src.with_suffix(suffix)
            src.rename(sniffed)
            src = sniffed

        if suffix not in WRITABLE_SUFFIXES:
            # WAV/AAC-raw cannot embed tags — reject early with a clear reason.
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
            await msg.answer(
                "⚠️ <b>این فرمت از تگ پشتیبانی نمی‌کند</b>\n\n"
                "فایل‌های WAV تگ داخلی ندارند. لطفاً فایل را به MP3 تبدیل کن "
                "و دوباره بفرست تا تگ و کاورش را ویرایش کنم 🎵"
            )
            await state.clear()
            return

        try:
            tags = read_tags(src)
        except MewviaError as exc:
            if status_msg:
                await status_msg.edit_text(error_text(exc.code))
            return
        except Exception as exc:
            log.warning("tag_editor_read_failed", error=str(exc))
            if status_msg:
                await status_msg.edit_text(error_text("unknown"))
            return

    await state.update_data(
        file_id=file_id,
        kind=kind.value,
        file_size=file_size,
        reply_to_id=reply_to_id,
        tag_title=None,
        tag_artist=None,
        tag_album=None,
        tag_genre=None,
        tag_track=None,
        cover_file_id=None,
    )

    registry = await ready_registry()
    card = format_tags_card(tags)
    kb = tag_editor_kb(registry)
    if status_msg:
        await status_msg.edit_text(card, reply_markup=kb.as_markup())
    else:
        await msg.answer(card, reply_markup=kb.as_markup())


@router.callback_query(TagFieldCB.filter(F.action == "field"))
async def prompt_tag_field(
    query: CallbackQuery, callback_data: TagFieldCB, state: FSMContext
) -> None:
    field = callback_data.field
    if field not in FIELD_LABELS:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    data = await state.get_data()
    if not data.get("file_id"):
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    # Toggle: tapping an already-staged field unstages it (tick disappears).
    staged_key = STAGED_KEY_BY_FIELD[field]
    if data.get(staged_key) is not None:
        await state.update_data({staged_key: None, "tag_active_field": None})
        await state.set_state(None)
        data = await state.get_data()
        summary = _staged_summary(data)
        registry = await ready_registry()
        kb = tag_editor_kb(registry, staged=data)
        label = FIELD_LABELS[field]
        text = (
            f"🗑️ <b>{label}</b> از لیست ویرایش حذف شد (تیک برداشته شد).\n\n"
            f"{summary}\n\nادامه بده یا «✅ ذخیره و ارسال» را بزن:"
        )
        if isinstance(query.message, Message):
            edited = await query.message.edit_text(text, reply_markup=kb.as_markup())
            if edited is None:
                await query.message.answer(text, reply_markup=kb.as_markup())
        await query.answer()
        return

    await state.update_data(tag_active_field=field)
    await state.set_state(TagEditFSM.waiting_for_value)

    registry = await ready_registry()
    kb = KB(registry)
    kb.row(
        inline_button(
            "❌ انصراف",
            callback_data=TagFieldCB(action="cancel").pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        )
    )

    label = FIELD_LABELS[field]
    hint = ""
    if field == "track":
        hint = f"\nیک عدد بین ۱ تا {fa_digits(str(MAX_TRACK))} بفرست (یا ۰ برای پاک کردن)."
    elif field in FIELD_LIMITS:
        hint = f"\nحداکثر {fa_digits(str(FIELD_LIMITS[field]))} کاراکتر."
    hint += "\nبرای پاک کردن مقدار فعلی، کلمه <code>حذف</code> را بفرست."

    prompt_text = f"<b>📝 ویرایش {label}</b>{hint}\n\nمقدار جدید را بفرست:"
    if isinstance(query.message, Message):
        edited = await query.message.edit_text(
            prompt_text,
            reply_markup=kb.as_markup(),
        )
        if edited is None:
            # Test doubles return None for edits — fall back to a reply.
            await query.message.answer(prompt_text, reply_markup=kb.as_markup())
    await query.answer()


@router.message(TagEditFSM.waiting_for_value)
async def receive_tag_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data.get("tag_active_field", "")
    if field not in FIELD_LABELS:
        await state.clear()
        await message.reply(t("common.stale_button"))
        return

    raw = (message.text or "").strip()
    if not raw:
        await message.reply("لطفاً یک متن بفرست (متن خالی قبول نیست).")
        return

    if raw in ("حذف", "delete", "clear", "-"):
        await state.update_data({f"tag_{field}": "__CLEAR__"})
        staged_note = "پاک خواهد شد 🗑️"
    elif field == "track":
        try:
            num = int(fa_to_en_digits(raw))
        except ValueError:
            await message.reply(
                f"شماره ترک باید عدد باشد (۱ تا {fa_digits(str(MAX_TRACK))}). دوباره بفرست:"
            )
            return
        if num == 0:
            await state.update_data(tag_track="__CLEAR__")
            staged_note = "پاک خواهد شد 🗑️"
        elif not 1 <= num <= MAX_TRACK:
            await message.reply(
                f"عدد باید بین ۱ تا {fa_digits(str(MAX_TRACK))} باشد. دوباره بفرست:"
            )
            return
        else:
            await state.update_data(tag_track=num)
            staged_note = f"<code>{fa_digits(str(num))}</code>"
    else:
        limit = FIELD_LIMITS[field]
        if len(raw) > limit:
            await message.reply(
                f"متنت {fa_digits(str(len(raw)))} کاراکتر است ولی "
                f"سقف {fa_digits(str(limit))} است. کوتاه‌تر بفرست:"
            )
            return
        await state.update_data({f"tag_{field}": raw})
        staged_note = f"«{html.quote(raw)}»"

    await state.set_state(None)  # back to idle, keep staged data
    data = await state.get_data()
    summary = _staged_summary(data)

    registry = await ready_registry()
    kb = tag_editor_kb(registry, staged=data)
    label = FIELD_LABELS[field]
    await message.reply(
        f"✅ <b>{label}</b> ثبت شد: {staged_note} ✅\n\n{summary}\n\n"
        "ادامه بده یا «✅ ذخیره و ارسال» را بزن:",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(TagFieldCB.filter(F.action == "cover"))
async def prompt_tag_cover(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("file_id"):
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    await state.set_state(TagEditFSM.waiting_for_cover)

    registry = await ready_registry()
    kb = KB(registry)
    kb.row(
        inline_button(
            "🗑️ حذف کامل کاور قبلی",
            callback_data=TagFieldCB(action="clear_cover").pack(),
            style=Style.PRIMARY,
            emoji_slot="trash",
            registry=registry,
        )
    )
    kb.row(
        inline_button(
            "❌ انصراف",
            callback_data=TagFieldCB(action="cancel").pack(),
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        )
    )
    if isinstance(query.message, Message):
        edited = await query.message.edit_text(
            "<b>🖼️ کاور جدید</b>\n\n"
            "یک <b>عکس</b> بفرست تا کاور آهنگ بشه (حداکثر ۵ مگابایت).\n"
            "عکس مربعی بهترین نتیجه رو میده.\n\n"
            "برای حذف کاور، دکمه «🗑️ حذف کامل کاور قبلی» را بزن.",
            reply_markup=kb.as_markup(),
        )
        if edited is None:
            await query.message.answer(
                "<b>🖼️ کاور جدید</b>\n\nیک <b>عکس</b> بفرست یا دکمه حذف را بزن.",
                reply_markup=kb.as_markup(),
            )
    await query.answer()


@router.callback_query(TagFieldCB.filter(F.action == "clear_cover"))
async def clear_tag_cover(query: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(cover_file_id="__CLEAR__")
    await state.set_state(None)
    data = await state.get_data()
    summary = _staged_summary(data)
    registry = await ready_registry()
    kb = tag_editor_kb(registry, staged=data)
    text = (
        f"🗑️ <b>کاور آهنگ برای حذف انتخاب شد</b> 🖼️ ✅\n\n"
        f"{summary}\n\nادامه بده یا «✅ ذخیره و ارسال» را بزن:"
    )
    if isinstance(query.message, Message):
        await query.message.edit_text(text, reply_markup=kb.as_markup())
    await query.answer()


@router.message(TagEditFSM.waiting_for_cover, F.photo)
async def receive_tag_cover(message: Message, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("file_id"):
        await state.clear()
        await message.reply(t("common.stale_button"))
        return

    if not message.photo:
        await message.reply("لطفاً یک عکس بفرست.")
        return

    photo = message.photo[-1]
    await state.update_data(cover_file_id=photo.file_id)
    await state.set_state(None)

    data = await state.get_data()
    summary = _staged_summary(data)
    registry = await ready_registry()
    kb = tag_editor_kb(registry, staged=data)
    await message.reply(
        f"✅ <b>کاور جدید</b> ثبت شد 🖼️ ✅\n\n{summary}\n\nادامه بده یا «✅ ذخیره و ارسال» را بزن:",
        reply_markup=kb.as_markup(),
    )


@router.message(TagEditFSM.waiting_for_cover)
async def receive_tag_cover_invalid(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw in ("حذف", "delete", "clear", "-"):
        await state.update_data(cover_file_id="__CLEAR__")
        await state.set_state(None)
        data = await state.get_data()
        summary = _staged_summary(data)
        registry = await ready_registry()
        kb = tag_editor_kb(registry, staged=data)
        await message.reply(
            f"🗑️ <b>کاور آهنگ برای حذف انتخاب شد</b> 🖼️ ✅\n\n"
            f"{summary}\n\nادامه بده یا «✅ ذخیره و ارسال» را بزن:",
            reply_markup=kb.as_markup(),
        )
        return
    await message.reply(
        "این کاور نشد! لطفاً یک <b>عکس</b> بفرست (یا برای حذف کاور، کلمه <code>حذف</code> را بفرست)."
    )


@router.callback_query(TagFieldCB.filter(F.action == "save"))
async def save_tag_edits(
    query: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    data = await state.get_data()
    file_id = data.get("file_id")
    if not file_id:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    staged = {
        "title": data.get("tag_title"),
        "artist": data.get("tag_artist"),
        "album": data.get("tag_album"),
        "genre": data.get("tag_genre"),
        "track": data.get("tag_track"),
    }
    cover_file_id = data.get("cover_file_id")
    if all(v is None for v in staged.values()) and not cover_file_id:
        await query.answer("هنوز چیزی ویرایش نکردی! اول یک فیلد را عوض کن.", show_alert=True)
        return

    msg = query.message
    await query.answer()
    await state.clear()

    status = await msg.edit_text("⏳ <b>در حال ذخیره تگ‌ها و آماده‌سازی فایل…</b>", reply_markup=None)
    status_msg: Message | None = status if isinstance(status, Message) else None

    start_time = time.perf_counter()
    conv_repo = ConversionRepo(session)
    user_id = query.from_user.id
    file_size = int(data.get("file_size", 0))
    reply_to_id = data.get("reply_to_id")
    kind_val = str(data.get("kind", "audio"))

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir:
        work_dir = Path(tmp_dir)
        src_file = work_dir / "tag_src"
        cover_file = work_dir / "cover_src"

        try:
            await bot.download(file_id, destination=src_file)
            # Preserve the original container extension for mutagen routing.
            orig_suffix = _sniff_audio_suffix(src_file)
            tagged = work_dir / f"tagged{orig_suffix}"
            src_file.rename(tagged)

            cover_bytes: bytes | None = None
            cover_mime: str | None = None
            if cover_file_id == "__CLEAR__":
                cover_bytes = b""
                cover_mime = ""
            elif cover_file_id:
                await bot.download(cover_file_id, destination=cover_file)
                cover_bytes, cover_mime = _prepare_cover(cover_file)

            kwargs: dict[str, object] = {}
            for key, val in staged.items():
                if val == "__CLEAR__":
                    kwargs[key] = ""
                elif val is not None:
                    kwargs[key] = val
            if cover_bytes is not None:
                kwargs["cover_bytes"] = cover_bytes
                kwargs["cover_mime"] = cover_mime

            final_tags = apply_tags(tagged, **kwargs)  # type: ignore[arg-type]

            out_obj = FSInputFile(tagged)
            caption_title = final_tags.title or "ویرایش‌شده با Mewvia 🎵"
            caption_artist = f" — {final_tags.artist}" if final_tags.artist else ""
            await bot.send_audio(
                chat_id=msg.chat.id,
                audio=out_obj,
                reply_to_message_id=reply_to_id,
                title=caption_title[:64],
                performer=(final_tags.artist or "")[:64],
                caption=f"🎵 <b>{html.quote(caption_title)}{html.quote(caption_artist)}</b>",
            )

            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass

            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            await conv_repo.log_conversion(
                telegram_id=user_id,
                source_kind=kind_val,
                target_id="atool_tag_edit",
                in_bytes=file_size,
                out_bytes=tagged.stat().st_size,
                duration_ms=elapsed_ms,
                status="success",
            )
        except MewviaError as exc:
            if status_msg:
                await status_msg.edit_text(error_text(exc.code))
            else:
                await msg.answer(error_text(exc.code))
        except Exception as exc:
            log.error("tag_editor_save_failed", error=str(exc))
            if status_msg:
                await status_msg.edit_text(error_text("unknown"))
            else:
                await msg.answer(error_text("unknown"))


@router.callback_query(TagFieldCB.filter(F.action == "cancel"))
async def cancel_tag_editor(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(query.message, Message):
        await query.message.edit_text("ویرایش تگ لغو شد. فایل اصلی دست‌نخورده موند ✅")
    await query.answer()


def _staged_summary(data: dict[str, object]) -> str:
    parts: list[str] = []
    labels = {**FIELD_LABELS, "cover_file_id": "کاور"}
    staged_map = {
        "title": data.get("tag_title"),
        "artist": data.get("tag_artist"),
        "album": data.get("tag_album"),
        "track": data.get("tag_track"),
        "genre": data.get("tag_genre"),
    }
    for key, val in staged_map.items():
        if val is None:
            continue
        shown = "🗑️ پاک میشه" if val == "__CLEAR__" else f"«{html.quote(str(val))}»"
        parts.append(f"• {labels[key]}: {shown}")
    if data.get("cover_file_id") == "__CLEAR__":
        parts.append("• کاور: 🗑️ پاک میشه")
    elif data.get("cover_file_id"):
        parts.append("• کاور: 🖼️ عکس جدید ثبت شد")
    if not parts:
        return "<i>هنوز چیزی ویرایش نشده.</i>"
    return "<b>موارد آماده ذخیره:</b>\n" + "\n".join(parts)


def _sniff_audio_suffix(path: Path) -> str:
    """Best-effort container detection for extension-less Telegram downloads."""
    try:
        handle = MutagenFile(path, easy=False)
        if handle is None:
            return ".mp3"
        mime = getattr(handle, "mime", []) or []
        mime_txt = " ".join(mime).lower()
        if "mp4" in mime_txt or "m4a" in mime_txt:
            return ".m4a"
        if "ogg" in mime_txt or "opus" in mime_txt or "vorbis" in mime_txt:
            return ".ogg"
        if "flac" in mime_txt:
            return ".flac"
        return ".mp3"
    except Exception:
        return ".mp3"


def _prepare_cover(path: Path) -> tuple[bytes, str]:
    """Validate + normalize cover art to JPEG ≤1000px."""
    data = path.read_bytes()
    if len(data) > 5 * 1024 * 1024:
        raise ConversionError("cover art too large (max 5 MB)")

    with PILImage.open(path) as opened:
        img = (
            opened.convert("RGBA") if opened.mode in ("RGBA", "LA", "P") else opened.convert("RGB")
        )
        bg = PILImage.new("RGB", img.size, (0, 0, 0))
        if img.mode == "RGBA":
            bg.paste(img, mask=img.split()[-1])
        else:
            bg.paste(img)
        bg.thumbnail((1000, 1000), Resampling.LANCZOS)
        out = Path(str(path) + ".cover.jpg")
        bg.save(out, format="JPEG", quality=88)
    return out.read_bytes(), "image/jpeg"


feature = register(
    Feature(
        name="tag_editor",
        router=router,
        order=99,
    )
)
