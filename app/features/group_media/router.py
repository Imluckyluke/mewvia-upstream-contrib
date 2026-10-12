"""Group media assistant router.

Handles `/media` and `/mewvia` commands sent in group/supergroup chats as replies to media messages.
Builds and sends interactive inline menus with ownership protection.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from app.callbacks import AudioToolCB, BatchCB, ConvertCB, FilterCB, TextOverlayCB, WideCB
from app.config import Settings
from app.core.logging import get_logger
from app.core.units import human_bytes
from app.features.registry import Feature, register
from app.media.kinds import MediaKind, detect_media_kind
from app.media.specs import allowed_targets_for
from app.middlewares.group_guard import store_panel
from app.ui.emoji import EmojiRegistry, get_emoji_registry
from app.ui.kb import KB, inline_button
from app.ui.styles import Style
from app.ui.texts import error_text, t

log = get_logger(__name__)

router = Router(name="group_media")


async def ready_registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


@router.message(
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
    Command("media"),
)
async def handle_group_media_command(message: Message, settings: Settings) -> None:
    reply = message.reply_to_message
    if not reply or not isinstance(reply, Message):
        await message.reply(t("group.no_reply"))
        return

    detected = detect_media_kind(reply)
    if not detected:
        await message.reply(t("group.unsupported_reply"))
        return

    kind, _media_obj, _file_id, file_size = detected

    if file_size > settings.download_limit:
        limit_str = human_bytes(settings.download_limit)
        await message.reply(error_text("file_too_large", limit=limit_str))
        return

    registry = await ready_registry()
    kb = KB(registry)

    # 1. Audio media (voice/audio) tools
    if kind in (MediaKind.VOICE, MediaKind.AUDIO):
        if kind == MediaKind.VOICE:
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
                    emoji_slot="lightning",
                    registry=registry,
                ),
            )
        else:  # AUDIO
            kb.row(
                inline_button(
                    "🗣 تبدیل به ویس",
                    callback_data=AudioToolCB(action="to_voice").pack(),
                    style=Style.PRIMARY,
                    emoji_slot="audio",
                    registry=registry,
                ),
                inline_button(
                    "⚡ تغییر سرعت موزیک",
                    callback_data=AudioToolCB(action="speed_menu").pack(),
                    style=Style.CONFIRM,
                    emoji_slot="lightning",
                    registry=registry,
                ),
            )
            kb.row(
                inline_button(
                    "🎵 ویرایش تگ و کاور",
                    callback_data=AudioToolCB(action="tag_menu").pack(),
                    style=Style.CONFIRM,
                    emoji_slot="edit",
                    registry=registry,
                )
            )

    # 2. Regular conversion targets
    targets = allowed_targets_for(kind)
    if targets:
        for target_spec in targets:
            kb.button(
                t(target_spec.label_key),
                callback_data=ConvertCB(kind=kind.value, target=target_spec.id).pack(),
                style=target_spec.style,
                emoji_slot=target_spec.emoji_slot,
            )
        kb.adjust(*([2] * (len(targets) // 2) + ([1] if len(targets) % 2 else [])))

    # 3. Video tools button if video
    if kind in (
        MediaKind.VIDEO,
        MediaKind.VIDEO_NOTE,
        MediaKind.ANIMATION,
        MediaKind.DOCUMENT_VIDEO,
    ):
        kb.row(
            inline_button(
                "🎬 ابزارهای ویدیو و ویدیومسیج",
                callback_data="vtool_action:start",
                style=Style.PRIMARY,
                emoji_slot="video",
                registry=registry,
            )
        )

    # 4. Image / Sticker / Video extra tools
    if kind in (
        MediaKind.PHOTO,
        MediaKind.STICKER_STATIC,
        MediaKind.DOCUMENT_IMAGE,
        MediaKind.VIDEO,
        MediaKind.ANIMATION,
        MediaKind.STICKER_VIDEO,
    ):
        kb.row(
            inline_button(
                "📝 افزودن متن / استیکرمانی",
                callback_data=TextOverlayCB(action="start").pack(),
                style=Style.PRIMARY,
                emoji_slot="text",
                registry=registry,
            ),
            inline_button(
                "↔️ واید کردن (پهنش کن!)",
                callback_data=WideCB(action="start").pack(),
                style=Style.CONFIRM,
                emoji_slot="wide",
                registry=registry,
            ),
        )
        kb.row(
            inline_button(
                "🎨 فیلترهای رنگی / افکت",
                callback_data=FilterCB(action="start").pack(),
                style=Style.PRIMARY,
                emoji_slot="filter",
                registry=registry,
            ),
            inline_button(
                "📌 ساخت/افزودن به استیکرپک من",
                callback_data="spack_action:start",
                style=Style.CONFIRM,
                emoji_slot="pack",
                registry=registry,
            ),
        )

    # Batch transformations apply to visual media only; audio gets its own tools row.
    if kind not in (MediaKind.VOICE, MediaKind.AUDIO):
        kb.row(
            inline_button(
                "⚙️ تغییرات دسته‌ای (چند کار همزمان)",
                callback_data=BatchCB(action="menu").pack(),
                style=Style.CONFIRM,
                emoji_slot="lightning",
                registry=registry,
            )
        )

    sender_name = message.from_user.first_name if message.from_user else "کاربر"
    title = t("group.panel_title", name=sender_name)

    sent_msg = await message.answer(
        title,
        reply_markup=kb.as_markup(),
        reply_to_message_id=reply.message_id,
    )

    # Store panel metadata so the guard locks the buttons
    # and resolve_panel_media recovers file_id
    owner_id = message.from_user.id if message.from_user else 0
    await store_panel(
        chat_id=message.chat.id,
        message_id=sent_msg.message_id,
        owner_id=owner_id,
        owner_name=sender_name,
        reply_to_message_id=reply.message_id,
        kind=kind.value,
        file_id=_file_id,
        file_size=file_size,
    )


feature = register(
    Feature(
        name="group_media",
        router=router,
        order=15,
    )
)
