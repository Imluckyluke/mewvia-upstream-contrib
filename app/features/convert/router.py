"""Media conversion feature router.

Handles incoming media files, presents target format choices via a colored inline
menu, executes conversions with pipeline, sends back results, and logs statistics.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import BatchCB, ConvertCB, FilterCB, MenuCB, TextOverlayCB, WideCB
from app.config import Settings
from app.core.errors import MewviaError
from app.core.logging import get_logger
from app.core.quota import check_daily_quota
from app.core.units import human_bytes
from app.db.repo import ConversionRepo
from app.features.registry import Feature, register
from app.media.kinds import MediaKind, detect_media_kind, resolve_panel_media
from app.media.pipeline import convert
from app.media.specs import (
    TARGET_GIF,
    TARGET_WEBM_STICKER,
    TARGET_WEBP,
    allowed_targets_for,
    get_target,
)
from app.middlewares.group_guard import store_panel
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.progress import ProgressController
from app.ui.styles import Style
from app.ui.texts import error_text, t

log = get_logger(__name__)

router = Router(name="convert")


async def ready_registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


@router.message(
    F.chat.type == "private",
    StateFilter(None),
    F.sticker | F.animation | F.video_note | F.video | F.photo | F.document,
)
async def handle_incoming_media(message: Message, settings: Settings) -> None:
    detected = detect_media_kind(message)
    if not detected:
        return

    kind, _media_obj, _file_id, file_size = detected

    # If it's audio (e.g. sent as uncompressed document), delegate to audio router
    if kind in (MediaKind.AUDIO, MediaKind.VOICE):
        return

    # Enforce API file download limits (20MB public, 2GB local)
    if file_size > settings.download_limit:
        limit_str = human_bytes(settings.download_limit)
        await message.reply(error_text("file_too_large", limit=limit_str))
        return

    targets = allowed_targets_for(kind)
    if not targets:
        await message.reply(error_text("unsupported_media"))
        return

    registry = await ready_registry()
    kb = KB(registry)

    # Build target selection inline buttons
    for target_spec in targets:
        kb.button(
            t(target_spec.label_key),
            callback_data=ConvertCB(kind=kind.value, target=target_spec.id).pack(),
            style=target_spec.style,
            emoji_slot=target_spec.emoji_slot,
        )

    # 2 buttons per row for targets
    kb.adjust(*([2] * (len(targets) // 2) + ([1] if len(targets) % 2 else [])))

    # Extra video tools button if video
    if kind in (
        MediaKind.VIDEO,
        MediaKind.VIDEO_NOTE,
        MediaKind.ANIMATION,
        MediaKind.DOCUMENT_VIDEO,
    ):
        kb.row(
            inline_button(
                "ابزارهای ویدیو و ویدیومسیج",
                callback_data="vtool_action:start",
                style=Style.PRIMARY,
                emoji_slot="video",
                registry=registry,
            )
        )

    # Add Batch Processing Button
    kb.row(
        inline_button(
            "تغییرات دسته‌ای (چند کار همزمان)",
            callback_data=BatchCB(action="menu").pack(),
            style=Style.CONFIRM,
            emoji_slot="tool",
            registry=registry,
        )
    )

    # Add Text Overlay, Wide Media, Filters & StickerPack action rows
    kb.row(
        inline_button(
            "افزودن متن / استیکرمانی",
            callback_data=TextOverlayCB(action="start").pack(),
            style=Style.PRIMARY,
            emoji_slot="text",
            registry=registry,
        ),
        inline_button(
            "واید کردن (پهنش کن!)",
            callback_data=WideCB(action="start").pack(),
            style=Style.CONFIRM,
            emoji_slot="wide",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "فیلترهای رنگی / افکت",
            callback_data=FilterCB(action="start").pack(),
            style=Style.PRIMARY,
            emoji_slot="filter",
            registry=registry,
        ),
        inline_button(
            "ساخت/افزودن به استیکرپک من",
            callback_data="spack_action:start",
            style=Style.CONFIRM,
            emoji_slot="pack",
            registry=registry,
        ),
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

    prompt = t("convert.choose_target")
    # Reply directly to the user's media message so query.message.reply_to_message is set
    sent_msg = await message.reply(prompt, reply_markup=kb.as_markup())
    if sent_msg is None:
        log.warning(
            "convert_prompt_send_failed", user=message.from_user.id if message.from_user else 0
        )
        return
    owner_id = message.from_user.id if message.from_user else 0
    owner_name = message.from_user.first_name if message.from_user else "کاربر"
    await store_panel(
        chat_id=message.chat.id,
        message_id=sent_msg.message_id,
        owner_id=owner_id,
        owner_name=owner_name,
        reply_to_message_id=message.message_id,
        kind=kind.value,
        file_id=_file_id,
        file_size=file_size,
    )


@router.callback_query(ConvertCB.filter())
async def process_conversion(
    query: CallbackQuery,
    callback_data: ConvertCB,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    target_spec = get_target(callback_data.target)
    if not target_spec:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

    try:
        kind = MediaKind(callback_data.kind)
    except ValueError:
        await query.answer(t("common.stale_button"), show_alert=True)
        return

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

    if not await check_daily_quota(session, user_id, query):
        return

    await query.answer()

    # Show processing status message or progress animation
    progress: ProgressController | Message | None = None
    try:
        progress = await ProgressController.start(
            bot=bot,
            chat_id=msg.chat.id,
            reply_to_id=reply_to_id or msg.message_id,
            initial_stage="در حال تبدیل فرمت مدیا…",
            session=session,
            replace_msg=msg if msg.text is not None else None,
        )
    except Exception as p_exc:
        log.warning("progress_controller_convert_start_failed", error=str(p_exc))
        status_edited = await msg.edit_text(t("common.processing"), reply_markup=None)
        progress = status_edited if isinstance(status_edited, Message) else None

    status_msg = progress

    start_time = time.perf_counter()
    conv_repo = ConversionRepo(session)

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_dir_str:
        work_dir = Path(tmp_dir_str)

        # Give source file a clear extension so ffmpeg can infer input format
        ext = ".bin"
        if kind in (MediaKind.PHOTO, MediaKind.DOCUMENT_IMAGE):
            ext = ".png"
        elif kind in (
            MediaKind.VIDEO,
            MediaKind.ANIMATION,
            MediaKind.VIDEO_NOTE,
            MediaKind.DOCUMENT_VIDEO,
        ):
            ext = ".mp4"
        elif kind == MediaKind.STICKER_ANIMATED:
            ext = ".tgs"
        elif kind == MediaKind.STICKER_VIDEO:
            ext = ".webm"
        elif kind == MediaKind.STICKER_STATIC:
            ext = ".webp"

        src_file = work_dir / f"input_media{ext}"

        try:
            # Download file from Telegram
            await bot.download(file_id, destination=src_file)

            # Execute pipeline conversion
            output = await convert(src_file, kind, target_spec, work_dir)

            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            out_bytes = output.info.file_size
            input_file_obj = FSInputFile(output.path)

            # Send back converted media according to format type
            chat_id = msg.chat.id

            if target_spec.id in (TARGET_WEBP.id, TARGET_WEBM_STICKER.id):
                await bot.send_sticker(
                    chat_id=chat_id, sticker=input_file_obj, reply_to_message_id=reply_to_id
                )
            elif target_spec.id == TARGET_GIF.id:
                await bot.send_animation(
                    chat_id=chat_id, animation=input_file_obj, reply_to_message_id=reply_to_id
                )
            elif target_spec.id == "png":
                await bot.send_photo(
                    chat_id=chat_id, photo=input_file_obj, reply_to_message_id=reply_to_id
                )
            elif target_spec.id == "mp4":
                await bot.send_video(
                    chat_id=chat_id, video=input_file_obj, reply_to_message_id=reply_to_id
                )
            else:
                await bot.send_document(
                    chat_id=chat_id, document=input_file_obj, reply_to_message_id=reply_to_id
                )

            # Clean up status message
            if status_msg is not None:
                try:
                    await status_msg.delete()
                except Exception:
                    pass

            # Log successful conversion
            await conv_repo.log_conversion(
                telegram_id=user_id,
                source_kind=kind.value,
                target_id=target_spec.id,
                in_bytes=file_size,
                out_bytes=out_bytes,
                duration_ms=elapsed_ms,
                status="success",
            )
            log.info(
                "conversion_success",
                user=user_id,
                kind=kind.value,
                target=target_spec.id,
                duration_ms=elapsed_ms,
            )

        except MewviaError as exc:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            err_msg = error_text(exc.code)
            if status_msg is not None:
                await status_msg.edit_text(err_msg)

            await conv_repo.log_conversion(
                telegram_id=user_id,
                source_kind=kind.value,
                target_id=target_spec.id,
                in_bytes=file_size,
                duration_ms=elapsed_ms,
                status="error",
                error_code=exc.code,
            )
            log.warning("conversion_error", user=user_id, error_code=exc.code)

        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            err_msg = error_text("unknown")
            if status_msg is not None:
                await status_msg.edit_text(err_msg)

            await conv_repo.log_conversion(
                telegram_id=user_id,
                source_kind=kind.value,
                target_id=target_spec.id,
                in_bytes=file_size,
                duration_ms=elapsed_ms,
                status="error",
                error_code="unknown",
            )
            log.error("conversion_unexpected_exception", user=user_id, error=str(exc))


@router.callback_query(MenuCB.filter(F.target == "convert"))
async def menu_convert(query: CallbackQuery) -> None:
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
            f"{registry.text('sticker')} {t('convert.guide_body')}", reply_markup=kb
        )
    await query.answer()


feature = register(
    Feature(
        name="convert",
        router=router,
        menu_label_key="menu.convert",
        menu_emoji_slot="sticker",
        menu_style=Style.PRIMARY,
        order=100,
    )
)
