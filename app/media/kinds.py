"""Media kinds detection and media processing engine."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from aiogram.types import CallbackQuery, Message

from app.middlewares.group_guard import get_panel


class MediaKind(StrEnum):
    """Media types supported as input by Mewvia."""

    STICKER_STATIC = "sticker_static"  # raster WEBP / PNG sticker
    STICKER_ANIMATED = "sticker_animated"  # TGS (Lottie vector animation)
    STICKER_VIDEO = "sticker_video"  # WEBM VP9 video sticker
    ANIMATION = "animation"  # Telegram MP4 GIF / document GIF
    VIDEO = "video"  # MP4 / MKV / MOV video
    VIDEO_NOTE = "video_note"  # Telegram round video message
    PHOTO = "photo"  # Telegram compressed photo
    DOCUMENT_IMAGE = "document_image"  # Image sent as uncompressed document
    DOCUMENT_VIDEO = "document_video"  # Video sent as uncompressed document
    VOICE = "voice"  # Telegram voice message
    AUDIO = "audio"  # Telegram audio file


def detect_media_kind(message: Message) -> tuple[MediaKind, Any, str, int] | None:
    """Inspect a Telegram message and return (MediaKind, media_object, file_id, file_size) or None.

    Handles photo, sticker, animation (GIF), video, video_note, and document.
    """
    if message.sticker:
        st = message.sticker
        file_id = st.file_id
        file_size = st.file_size or 0
        if st.is_animated:
            return MediaKind.STICKER_ANIMATED, st, file_id, file_size
        if st.is_video:
            return MediaKind.STICKER_VIDEO, st, file_id, file_size
        return MediaKind.STICKER_STATIC, st, file_id, file_size

    if message.animation:
        anim = message.animation
        return MediaKind.ANIMATION, anim, anim.file_id, anim.file_size or 0

    if message.video_note:
        vn = message.video_note
        return MediaKind.VIDEO_NOTE, vn, vn.file_id, vn.file_size or 0

    if message.video:
        vid = message.video
        return MediaKind.VIDEO, vid, vid.file_id, vid.file_size or 0

    if message.photo:
        # Highest resolution photo is last in the list
        ph = message.photo[-1]
        return MediaKind.PHOTO, ph, ph.file_id, ph.file_size or 0

    if message.voice:
        v = message.voice
        return MediaKind.VOICE, v, v.file_id, v.file_size or 0

    if message.audio:
        a = message.audio
        return MediaKind.AUDIO, a, a.file_id, a.file_size or 0

    if message.document:
        doc = message.document
        mime = (doc.mime_type or "").lower()
        file_id = doc.file_id
        file_size = doc.file_size or 0
        file_name = (doc.file_name or "").lower()

        if mime.startswith("image/") or file_name.endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        ):
            return MediaKind.DOCUMENT_IMAGE, doc, file_id, file_size
        if (
            mime.startswith("video/")
            or mime == "image/gif"
            or file_name.endswith((".mp4", ".mov", ".mkv", ".avi", ".webm", ".gif"))
        ):
            return MediaKind.DOCUMENT_VIDEO, doc, file_id, file_size
        if mime == "application/x-tgsticker" or file_name.endswith(".tgs"):
            return MediaKind.STICKER_ANIMATED, doc, file_id, file_size
        if mime.startswith("audio/") or file_name.endswith(
            (".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus")
        ):
            return MediaKind.AUDIO, doc, file_id, file_size

    return None


def resolve_media_message(
    message: Message,
) -> tuple[tuple[MediaKind, Any, str, int], Message] | None:
    """Inspect a message and its reply chain to locate the target media message.

    Checks:
    1. message.reply_to_message (direct reply to media)
    2. message.reply_to_message.reply_to_message (chained reply: bot menu -> command -> media)
    3. message itself

    Returns ((kind, media_obj, file_id, file_size), target_message) or None.
    """
    if message.reply_to_message and isinstance(message.reply_to_message, Message):
        detected = detect_media_kind(message.reply_to_message)
        if detected:
            return detected, message.reply_to_message

        if message.reply_to_message.reply_to_message and isinstance(
            message.reply_to_message.reply_to_message, Message
        ):
            detected_chained = detect_media_kind(message.reply_to_message.reply_to_message)
            if detected_chained:
                return detected_chained, message.reply_to_message.reply_to_message

    detected_self = detect_media_kind(message)
    if detected_self:
        return detected_self, message

    return None


async def resolve_panel_media(
    query: CallbackQuery,
) -> tuple[MediaKind, str, int, int | None] | None:
    """Async resolver for CallbackQuery updates.

    Telegram API never populates reply_to_message in CallbackQuery.message updates.
    This helper checks:
    1. The stored panel payload (Redis/Memory) via get_panel(chat_id, message_id).
    2. query.message.reply_to_message if present.
    3. query.message itself.

    Returns (kind, file_id, file_size, reply_to_message_id) or None.
    """
    if not query.message or not isinstance(query.message, Message):
        return None

    msg = query.message
    panel = await get_panel(msg.chat.id, msg.message_id)
    if panel and panel.get("file_id") and panel.get("kind"):
        try:
            kind = MediaKind(panel["kind"])
            file_id = str(panel["file_id"])
            file_size = int(panel.get("file_size", 0))
            reply_to_id = panel.get("reply_to_message_id")
            return kind, file_id, file_size, reply_to_id
        except ValueError:
            pass

    # Fallback to standard message inspection if panel record is absent
    resolved = resolve_media_message(msg)
    if resolved:
        (kind, _obj, file_id, file_size), target_msg = resolved
        reply_to_id = target_msg.message_id if target_msg else None
        return kind, file_id, file_size, reply_to_id

    return None


async def send_processing_status(msg: Message, text: str) -> Message | None:
    """Safely show a processing indicator on either a text menu or media message."""
    if msg.text is not None:
        try:
            res = await msg.edit_text(text, reply_markup=None)
            if isinstance(res, Message):
                return res
        except Exception:
            pass
        return None

    try:
        res = await msg.reply(text)
        if isinstance(res, Message):
            return res
    except Exception:
        pass
    return None
