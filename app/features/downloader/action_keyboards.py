"""Next-step interactive action keyboards attached directly to downloaded media.

Allows users to immediately convert, transform, edit or inspect downloaded files
(e.g., turn downloaded video into a round video note or extract MP3 audio,
convert music to a voice bubble, or apply filters/wide to photos) with a single tap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.callbacks import AudioToolCB, FilterCB, TextOverlayCB, VideoToolCB, WideCB
from app.ui.kb import KB, inline_button
from app.ui.styles import Style

if TYPE_CHECKING:
    from app.ui.emoji import EmojiRegistry


def build_video_actions_kb(registry: EmojiRegistry) -> KB:
    """Build next-step actions for sent videos."""
    kb = KB(registry)
    kb.row(
        inline_button(
            "ویدیومسیج گرد",
            callback_data=VideoToolCB(action="note").pack(),
            style=Style.CONFIRM,
            emoji_slot="video_note",
            registry=registry,
        ),
        inline_button(
            "استخراج صدا",
            callback_data=VideoToolCB(action="extract").pack(),
            style=Style.PRIMARY,
            emoji_slot="audio",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "فشرده‌سازی",
            callback_data=VideoToolCB(action="compress_menu").pack(),
            style=Style.NEUTRAL,
            emoji_slot="compress",
            registry=registry,
        ),
        inline_button(
            "ابزارهای ویدیو",
            callback_data="vtool_action:start",
            style=Style.PRIMARY,
            emoji_slot="tool",
            registry=registry,
        ),
    )
    return kb


def build_audio_actions_kb(registry: EmojiRegistry) -> KB:
    """Build next-step actions for sent audio tracks."""
    kb = KB(registry)
    kb.row(
        inline_button(
            "تبدیل به ویس",
            callback_data=AudioToolCB(action="to_voice").pack(),
            style=Style.CONFIRM,
            emoji_slot="mic",
            registry=registry,
        ),
        inline_button(
            "تغییر سرعت",
            callback_data=AudioToolCB(action="speed_menu").pack(),
            style=Style.PRIMARY,
            emoji_slot="lightning",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "ویرایش تگ و کاور",
            callback_data=AudioToolCB(action="tag_menu").pack(),
            style=Style.PRIMARY,
            emoji_slot="edit",
            registry=registry,
        ),
    )
    return kb


def build_photo_actions_kb(registry: EmojiRegistry) -> KB:
    """Build next-step actions for sent photos."""
    kb = KB(registry)
    kb.row(
        inline_button(
            "واید کردن (پهن)",
            callback_data=WideCB(action="start").pack(),
            style=Style.CONFIRM,
            emoji_slot="wide",
            registry=registry,
        ),
        inline_button(
            "فیلترهای رنگی",
            callback_data=FilterCB(action="start").pack(),
            style=Style.PRIMARY,
            emoji_slot="filter",
            registry=registry,
        ),
    )
    kb.row(
        inline_button(
            "افزودن به استیکرپک",
            callback_data="spack_action:start",
            style=Style.CONFIRM,
            emoji_slot="pack",
            registry=registry,
        ),
        inline_button(
            "افزودن متن روی عکس",
            callback_data=TextOverlayCB(action="start").pack(),
            style=Style.PRIMARY,
            emoji_slot="text",
            registry=registry,
        ),
    )
    return kb
