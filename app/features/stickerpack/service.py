"""Sticker set service interacting with Telegram Bot API methods."""

from __future__ import annotations

import uuid
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile, InputSticker

from app.core.errors import MewviaError
from app.core.logging import get_logger

log = get_logger(__name__)


def make_pack_name(user_id: int, title: str, bot_username: str) -> str:
    """Format sticker set name strictly adhering to Telegram rules.

    Must end in `_by_<bot_username>`, start with a letter, and contain only
    English letters/digits/underscores.
    Format: u{user_id}_{short_hex}_by_{bot_username}
    """
    clean_username = bot_username.lstrip("@")
    short_hex = uuid.uuid4().hex[:6]
    return f"u{user_id}_{short_hex}_by_{clean_username}"


def make_pack_title(title: str, bot_username: str) -> str:
    """Format sticker set title with bot username branding.

    Format: "{title} | @{bot_username}"
    """
    clean_username = bot_username.lstrip("@")
    return f"{title} | @{clean_username}"


async def create_user_sticker_set(
    bot: Bot,
    user_id: int,
    pack_name: str,
    title: str,
    sticker_path: Path,
    emoji: str,
    sticker_format: str = "video",  # static, video, animated
    sticker_type: str = "regular",  # regular, custom_emoji
) -> bool:
    """Create a new Telegram sticker set owned by user_id."""
    input_sticker = InputSticker(
        sticker=FSInputFile(sticker_path),
        format=sticker_format,
        emoji_list=[emoji],
    )

    try:
        res = await bot.create_new_sticker_set(
            user_id=user_id,
            name=pack_name,
            title=title,
            stickers=[input_sticker],
            sticker_type=sticker_type,
        )
        return bool(res)
    except TelegramAPIError as exc:
        log.error("create_sticker_set_failed", error=str(exc), pack=pack_name)
        raise MewviaError(
            f"ایجاد استیکرپک در تلگرام موفق نبود: {exc}", code="sticker_pack_failed"
        ) from exc


async def add_sticker_to_user_set(
    bot: Bot,
    user_id: int,
    pack_name: str,
    sticker_path: Path,
    emoji: str,
    sticker_format: str = "video",
) -> bool:
    """Add a sticker to an existing Telegram sticker set."""
    input_sticker = InputSticker(
        sticker=FSInputFile(sticker_path),
        format=sticker_format,
        emoji_list=[emoji],
    )

    try:
        res = await bot.add_sticker_to_set(
            user_id=user_id,
            name=pack_name,
            sticker=input_sticker,
        )
        return bool(res)
    except TelegramAPIError as exc:
        log.error("add_sticker_to_set_failed", error=str(exc), pack=pack_name)
        raise MewviaError(
            f"افزودن استیکر به پک موفق نبود: {exc}", code="add_sticker_failed"
        ) from exc
