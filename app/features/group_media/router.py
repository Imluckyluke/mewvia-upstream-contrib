"""Group media assistant router.

Handles `/media`, `/mewvia`, `/edit`, `/tools` commands and bot mentions (`@Mewvia_bot`)
sent in group/supergroup chats either as replies to media messages or directly with media.
Builds and sends interactive inline menus with ownership protection.
"""

from __future__ import annotations

import re

from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import BaseFilter
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


class GroupMediaInvocationFilter(BaseFilter):
    """Matches group messages that explicitly invoke the media assistant."""

    SUPPORTED_COMMANDS = frozenset({"media", "mewvia", "edit", "tools", "ابزار", "پنل"})
    # Historical public username; kept as an alias because deployments and
    # older group posts may use it even if BOT_USERNAME drifts.
    LEGACY_USERNAME = "mewvia_bot"

    async def __call__(self, message: Message, settings: Settings) -> bool:
        if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            return False

        text = (message.text or message.caption or "").strip()
        bot_user = (settings.bot_username or "").lower().lstrip("@")
        usernames = {self.LEGACY_USERNAME}
        if bot_user:
            usernames.add(bot_user)

        # 1. Explicit slash commands, addressed to this bot (or to nobody).
        if text.startswith("/"):
            command_part = text.split()[0][1:].lower()
            cmd_name, _, at_bot = command_part.partition("@")
            if at_bot and at_bot not in usernames:
                return False
            if cmd_name in self.SUPPORTED_COMMANDS:
                return True

        # 2. Explicit bot mention in text or caption (e.g. @Mewvia_bot).
        # Match the complete username so mentioning @MewviaBotNews is not
        # mistaken for a call to this bot.
        if any(
            re.search(rf"@{re.escape(name)}(?!\w)", text, flags=re.IGNORECASE) for name in usernames
        ):
            return True

        # NOTE: replying to a bot message is deliberately NOT a trigger.
        # Downloaded media / bot results live in the group; a plain reply to them
        # is normal chatter and must never pop the media panel open.
        return False


async def ready_registry() -> EmojiRegistry:
    registry = get_emoji_registry()
    await registry.refresh_premium_flag()
    await registry.load()
    return registry


@router.message(GroupMediaInvocationFilter())
async def handle_group_media_command(message: Message, settings: Settings) -> None:
    reply = message.reply_to_message
    target_msg: Message | None = None

    # Priority 1: User replied to a message containing media
    if reply and isinstance(reply, Message) and detect_media_kind(reply):
        target_msg = reply
    # Priority 2: Message itself contains media (e.g. caption with @Mewvia_bot or /media)
    elif detect_media_kind(message):
        target_msg = message

    if target_msg is None:
        if reply and isinstance(reply, Message):
            await message.reply(t("group.unsupported_reply"))
        else:
            await message.reply(t("group.no_reply"))
        return

    detected = detect_media_kind(target_msg)
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
                    "تبدیل ویس به MP3",
                    callback_data=AudioToolCB(action="to_mp3").pack(),
                    style=Style.PRIMARY,
                    emoji_slot="audio",
                    registry=registry,
                ),
                inline_button(
                    "تغییر سرعت ویس",
                    callback_data=AudioToolCB(action="speed_menu").pack(),
                    style=Style.CONFIRM,
                    emoji_slot="lightning",
                    registry=registry,
                ),
            )
            kb.row(
                inline_button(
                    "🔍 این چه آهنگیه؟ (Shazam)",
                    callback_data=AudioToolCB(action="shazam").pack(),
                    style=Style.PRIMARY,
                    emoji_slot="shazam",
                    registry=registry,
                )
            )
        else:  # AUDIO
            kb.row(
                inline_button(
                    "تبدیل به ویس",
                    callback_data=AudioToolCB(action="to_voice").pack(),
                    style=Style.PRIMARY,
                    emoji_slot="mic",
                    registry=registry,
                ),
                inline_button(
                    "تغییر سرعت موزیک",
                    callback_data=AudioToolCB(action="speed_menu").pack(),
                    style=Style.CONFIRM,
                    emoji_slot="lightning",
                    registry=registry,
                ),
            )
            kb.row(
                inline_button(
                    "ویرایش تگ و کاور",
                    callback_data=AudioToolCB(action="tag_menu").pack(),
                    style=Style.CONFIRM,
                    emoji_slot="edit",
                    registry=registry,
                ),
                inline_button(
                    "🔍 این چه آهنگیه؟ (Shazam)",
                    callback_data=AudioToolCB(action="shazam").pack(),
                    style=Style.PRIMARY,
                    emoji_slot="shazam",
                    registry=registry,
                ),
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
                "ابزارهای ویدیو و ویدیومسیج",
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

    # Batch transformations apply to visual media only; audio gets its own tools row.
    if kind not in (MediaKind.VOICE, MediaKind.AUDIO):
        kb.row(
            inline_button(
                "تغییرات دسته‌ای (چند کار همزمان)",
                callback_data=BatchCB(action="menu").pack(),
                style=Style.CONFIRM,
                emoji_slot="tool",
                registry=registry,
            )
        )

    reply_target_id = target_msg.message_id
    sender_name = message.from_user.first_name if message.from_user else "کاربر"
    title = t("group.panel_title", name=sender_name)

    sent_msg = await message.answer(
        title,
        reply_markup=kb.as_markup(),
        reply_to_message_id=reply_target_id,
    )
    if sent_msg is None:
        return

    # Store panel metadata so the guard locks the buttons
    # and resolve_panel_media recovers file_id
    owner_id = message.from_user.id if message.from_user else 0
    await store_panel(
        chat_id=message.chat.id,
        message_id=sent_msg.message_id,
        owner_id=owner_id,
        owner_name=sender_name,
        reply_to_message_id=reply_target_id,
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
