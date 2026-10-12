"""Phase-4 acceptance tests: media conversion flow end-to-end.

Tests media detection, target option keyboard generation, and conversion dispatch.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from aiogram import Dispatcher
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    PhotoSize,
    Sticker,
    Update,
)
from aiogram.types import User as TgUser
from app.callbacks import ConvertCB, MenuCB
from app.db.repo import ConversionRepo
from app.media.kinds import MediaKind, detect_media_kind
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fixtures.gen import generate_png_fixture
from tests.test_phase2_menu import RecordingBot

USER_ID = 999_000_000_088


def make_photo_update(update_id: int, user_id: int) -> Update:
    user = TgUser(id=user_id, is_bot=False, first_name="MediaUser")
    message = Message(
        message_id=update_id,
        date=datetime.datetime.now(datetime.UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=user,
        photo=[
            PhotoSize(
                file_id="ph_small",
                file_unique_id="u1",
                width=100,
                height=100,
                file_size=1000,
            ),
            PhotoSize(
                file_id="ph_large",
                file_unique_id="u2",
                width=800,
                height=600,
                file_size=50000,
            ),
        ],
    )
    return Update(update_id=update_id, message=message)


def make_sticker_update(update_id: int, user_id: int, *, is_video: bool = False) -> Update:
    user = TgUser(id=user_id, is_bot=False, first_name="MediaUser")
    st = Sticker(
        file_id="st_123",
        file_unique_id="u_st",
        type="regular",
        width=512,
        height=512,
        is_animated=False,
        is_video=is_video,
        file_size=10000,
    )
    message = Message(
        message_id=update_id,
        date=datetime.datetime.now(datetime.UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=user,
        sticker=st,
    )
    return Update(update_id=update_id, message=message)


# ---------------------------------------------------------------------------
# Media Detection
# ---------------------------------------------------------------------------


def test_detect_media_kind_photo() -> None:
    update = make_photo_update(1, USER_ID)
    msg = update.message
    assert msg is not None

    res = detect_media_kind(msg)
    assert res is not None
    kind, _obj, file_id, file_size = res
    assert kind == MediaKind.PHOTO
    assert file_id == "ph_large"
    assert file_size == 50000


def test_detect_media_kind_sticker() -> None:
    update = make_sticker_update(1, USER_ID)
    msg = update.message
    assert msg is not None

    res = detect_media_kind(msg)
    assert res is not None
    kind, _obj, file_id, _size = res
    assert kind == MediaKind.STICKER_STATIC
    assert file_id == "st_123"


# ---------------------------------------------------------------------------
# Target Selection Menu
# ---------------------------------------------------------------------------


async def test_incoming_photo_shows_target_menu(app_dispatcher: Dispatcher) -> None:
    bot = RecordingBot()
    try:
        await app_dispatcher.feed_update(bot, make_photo_update(1, USER_ID))
    finally:
        await bot.session.close()

    assert len(bot.sent) == 1
    sent_msg = bot.sent[0]
    markup = sent_msg.reply_markup
    assert isinstance(markup, InlineKeyboardMarkup)

    # Photo accepts png, webp, webm_sticker
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert any(d and "png" in d for d in datas)
    assert any(d and "webp" in d for d in datas)
    assert any(d and "webm_sticker" in d for d in datas)


async def test_menu_convert_guide_action(app_dispatcher: Dispatcher) -> None:
    """Tapping 'تبدیل مدیا' in main menu renders the guide page."""
    bot = RecordingBot()
    try:
        cb = CallbackQuery(
            id="c_conv",
            from_user=TgUser(id=USER_ID, is_bot=False, first_name="User"),
            message=Message(
                message_id=10,
                date=datetime.datetime.now(datetime.UTC),
                chat=Chat(id=USER_ID, type="private"),
                text="Main Menu",
            ),
            chat_instance="ci",
            data=MenuCB(target="convert").pack(),
        )
        await app_dispatcher.feed_update(bot, Update(update_id=1, callback_query=cb))
    finally:
        await bot.session.close()

    assert len(bot.sent) >= 1
    assert any("تبدیل مدیا" in str(m) for m in bot.sent)


# ---------------------------------------------------------------------------
# Mock Download & Conversion Dispatch
# ---------------------------------------------------------------------------


async def test_conversion_flow_dispatches_proper_send_method(
    app_dispatcher: Dispatcher, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mock download to return a real test fixture PNG file
    async def fake_download(
        self: RecordingBot, file_id: str, destination: Path, **kwargs: object
    ) -> Path:
        generate_png_fixture(destination, 512, 512)
        return destination

    monkeypatch.setattr(RecordingBot, "download", fake_download)

    bot = RecordingBot()
    try:
        # Step 1: Send photo update -> get menu
        photo_update = make_photo_update(1, USER_ID)
        await app_dispatcher.feed_update(bot, photo_update)
        assert len(bot.sent) == 1

        photo_msg = photo_update.message
        assert photo_msg is not None

        # Build a Message object representing the prompt message sent by bot
        prompt_msg = Message(
            message_id=2,
            date=datetime.datetime.now(datetime.UTC),
            chat=Chat(id=USER_ID, type="private"),
            from_user=TgUser(id=999, is_bot=True, first_name="Bot"),
            text="Choose target:",
            reply_markup=bot.sent[0].reply_markup,  # type: ignore
            reply_to_message=photo_msg,
        )

        cb = CallbackQuery(
            id="cb_1",
            from_user=TgUser(id=USER_ID, is_bot=False, first_name="MediaUser"),
            message=prompt_msg,
            chat_instance="ci_1",
            data=ConvertCB(kind=MediaKind.PHOTO.value, target="webp").pack(),
        )
        await app_dispatcher.feed_update(bot, Update(update_id=2, callback_query=cb))

    finally:
        await bot.session.close()

    # Verify send_sticker was invoked
    sent_methods = [type(m).__name__ for m in bot.sent]
    assert "SendSticker" in sent_methods

    # Verify DB conversion log entry
    repo = ConversionRepo(session)
    total = await repo.count_total()
    succ = await repo.count_successful()
    assert total >= 1
    assert succ >= 1
