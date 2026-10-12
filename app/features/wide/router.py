"""Wide media feature router."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import WideCB
from app.config import Settings
from app.core.logging import get_logger
from app.db.repo import ConversionRepo
from app.media.kinds import MediaKind, resolve_panel_media
from app.media.wide import make_image_wide, make_video_wide
from app.ui.texts import t

log = get_logger(__name__)

router = Router(name="wide")


@router.callback_query(WideCB.filter(F.action == "start"))
async def process_wide_media(
    query: CallbackQuery,
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

    kind, file_id, file_size, _reply_to_id = resolved
    user_id = query.from_user.id if query.from_user else 0
    await query.answer()

    status_msg = await msg.edit_text(t("common.processing"))

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir_str:
        work_dir = Path(tmp_dir_str)
        is_video_media = kind in (
            MediaKind.VIDEO,
            MediaKind.VIDEO_NOTE,
            MediaKind.ANIMATION,
            MediaKind.DOCUMENT_VIDEO,
            MediaKind.STICKER_VIDEO,
        )

        in_ext = ".bin"
        if kind in (MediaKind.PHOTO, MediaKind.DOCUMENT_IMAGE):
            in_ext = ".png"
        elif kind in (
            MediaKind.VIDEO,
            MediaKind.ANIMATION,
            MediaKind.VIDEO_NOTE,
            MediaKind.DOCUMENT_VIDEO,
        ):
            in_ext = ".mp4"
        elif kind == MediaKind.STICKER_ANIMATED:
            in_ext = ".tgs"
        elif kind == MediaKind.STICKER_VIDEO:
            in_ext = ".webm"
        elif kind == MediaKind.STICKER_STATIC:
            in_ext = ".webp"

        out_ext = ".mp4" if is_video_media else ".png"
        if kind == MediaKind.STICKER_STATIC:
            out_ext = ".webp"
        elif kind == MediaKind.STICKER_VIDEO:
            out_ext = ".webm"

        src_file = work_dir / f"input{in_ext}"
        out_file = work_dir / f"output{out_ext}"

        try:
            await bot.download(file_id, destination=src_file)

            start_time = time.perf_counter()
            if is_video_media:
                await make_video_wide(src_file, out_file)
            else:
                await make_image_wide(src_file, out_file)
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)

            input_file_obj = FSInputFile(out_file)
            chat_id = msg.chat.id

            if out_ext in (".webp", ".webm"):
                await bot.send_sticker(chat_id=chat_id, sticker=input_file_obj)
            elif out_ext == ".png":
                await bot.send_photo(chat_id=chat_id, photo=input_file_obj)
            else:
                await bot.send_document(chat_id=chat_id, document=input_file_obj)

            try:
                out_bytes = out_file.stat().st_size
            except OSError:
                out_bytes = 0
            await ConversionRepo(session).log_conversion(
                telegram_id=user_id,
                source_kind=kind.value,
                target_id="wide",
                in_bytes=file_size,
                out_bytes=out_bytes,
                duration_ms=elapsed_ms,
                status="success",
            )

            if isinstance(status_msg, Message):
                try:
                    await status_msg.delete()
                except Exception:
                    pass

        except Exception as exc:
            log.error("wide_media_error", error=str(exc))
            if isinstance(status_msg, Message):
                await status_msg.edit_text("خطا در پهن کردن (Wide) فایل.")
