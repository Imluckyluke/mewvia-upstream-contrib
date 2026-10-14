"""Visual filters feature router."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import FilterCB
from app.config import Settings
from app.core.logging import get_logger
from app.db.repo import ConversionRepo
from app.media.filters import FILTER_MAP, apply_filter_to_image, apply_filter_to_video
from app.media.kinds import MediaKind, resolve_panel_media
from app.media.pipeline import _convert_from_tgs
from app.media.specs import TARGET_MP4
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.styles import Style
from app.ui.texts import t

log = get_logger(__name__)

router = Router(name="filters")


async def ready_registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


@router.callback_query(FilterCB.filter(F.action == "start"))
async def show_filters_menu(
    query: CallbackQuery,
) -> None:
    if not isinstance(query.message, Message):
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    registry = await ready_registry()
    kb = KB(registry)

    # Add filter option buttons (2 per row)
    buttons = []
    for f_id, f_spec in FILTER_MAP.items():
        buttons.append(
            inline_button(
                f_spec.label,
                callback_data=FilterCB(action="apply", filter_id=f_id).pack(),
                style=Style.PRIMARY,
                emoji_slot=f_spec.emoji_slot,
                registry=registry,
            )
        )

    for i in range(0, len(buttons), 2):
        kb.row(*buttons[i : i + 2])

    kb.row(
        inline_button(
            t("common.cancel"),
            callback_data="menu:root",
            style=Style.DESTRUCTIVE,
            emoji_slot="error",
            registry=registry,
        )
    )

    await query.answer()
    await query.message.edit_text(
        f"{registry.text('filter')} <b>فیلتر مورد نظر خود را انتخاب کنید:</b>",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(FilterCB.filter(F.action == "apply"))
async def process_filter_apply(
    query: CallbackQuery,
    callback_data: FilterCB,
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
    filter_id = callback_data.filter_id

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

            if kind == MediaKind.STICKER_ANIMATED:
                temp_mp4 = work_dir / "tgs_temp.mp4"
                await _convert_from_tgs(src_file, TARGET_MP4, temp_mp4, work_dir)
                src_file = temp_mp4
                is_video_media = True
                out_ext = ".webm"
                out_file = work_dir / f"output{out_ext}"

            start_time = time.perf_counter()
            if is_video_media:
                await apply_filter_to_video(src_file, out_file, filter_id)
            else:
                await apply_filter_to_image(src_file, out_file, filter_id)
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
                target_id=f"filter_{filter_id}",
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
            log.error("filter_media_error", error=str(exc))
            if isinstance(status_msg, Message):
                await status_msg.edit_text("خطا در اعمال فیلتر روی فایل.")
