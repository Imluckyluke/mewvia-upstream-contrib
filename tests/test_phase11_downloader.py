"""Acceptance & unit tests for the social media downloader feature."""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Dispatcher
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, Update
from aiogram.types import User as TgUser
from app.callbacks.downloader import DownloaderCB
from app.db.repo import ConversionRepo, UserRepo
from app.media.downloader import (
    DownloadInfo,
    create_token,
    detect_platform,
    extract_first_url,
    get_download_context,
    store_download_context,
)
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_sponsor import RecordingBot

USER_ID = 888_000_111


def test_detect_platform_known_domains() -> None:
    cases = [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "🎬"),
        ("https://youtu.be/dQw4w9WgXcQ", "youtube", "🎬"),
        ("https://instagram.com/reel/C_abc123/", "instagram", "📱"),
        ("https://www.tiktok.com/@user/video/1234567", "tiktok", "🎵"),
        ("https://x.com/jack/status/20", "twitter", "🐦"),
        ("https://twitter.com/jack/status/20", "twitter", "🐦"),
        ("https://pin.it/7xTest", "pinterest", "📌"),
        ("https://soundcloud.com/artist/track", "soundcloud", "🎧"),
        ("https://reddit.com/r/videos/comments/xyz", "reddit", "🤖"),
        ("https://www.aparat.com/v/abcd", "aparat", "📺"),
        ("https://example.com/video.mp4", "other", "🌐"),
    ]
    for url, expected_key, expected_icon in cases:
        key, name, icon = detect_platform(url)
        assert key == expected_key, f"Failed key for {url}"
        assert icon == expected_icon, f"Failed icon for {url}"
        assert name, f"Missing name for {url}"


def test_extract_first_url() -> None:
    assert extract_first_url("این رو ببین: https://youtu.be/xyz دمت گرم") == "https://youtu.be/xyz"
    assert extract_first_url("پیام بدون لینک سلام چطوری") is None
    assert extract_first_url("http://example.com/test") == "http://example.com/test"


async def test_token_storage_roundtrip() -> None:
    token = create_token()
    assert len(token) == 8
    payload = {"url": "https://test.com", "title": "Test Title"}
    await store_download_context(token, payload)
    retrieved = await get_download_context(token)
    assert retrieved == payload


def test_downloader_cb_pack_unpack() -> None:
    cb = DownloaderCB(action="video", token="a1b2c3d4")
    packed = cb.pack()
    assert len(packed) < 64
    unpacked = DownloaderCB.unpack(packed)
    assert unpacked.action == "video"
    assert unpacked.token == "a1b2c3d4"


async def test_downloader_url_probe_and_card(
    app_dispatcher: Dispatcher, session: AsyncSession
) -> None:
    """Sending a YouTube media URL prompts the user with interactive quality options."""
    bot = RecordingBot()
    fake_info = DownloadInfo(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        platform_key="youtube",
        platform_name="یوتیوب",
        platform_icon="🔴",
        title="تست ویدیوی یوتیوب",
        duration_seconds=30,
        duration_str="0:30",
        uploader="creator_account",
        thumbnail_url=None,
        is_video=True,
        is_audio_only=False,
        is_gallery=False,
    )

    msg = Message(
        message_id=10,
        date=datetime.datetime.now(datetime.UTC),
        chat=Chat(id=USER_ID, type="private"),
        from_user=TgUser(id=USER_ID, is_bot=False, first_name="User"),
        text="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )

    try:
        with patch(
            "app.features.downloader.router.probe_media_url", new=AsyncMock(return_value=fake_info)
        ):
            await app_dispatcher.feed_update(bot, Update(update_id=10, message=msg))
    finally:
        await bot.session.close()

    # Bot replies with status and then edits with summary card
    edits = [c for c in bot.calls if type(c).__name__ == "EditMessageText"]
    if not edits:
        # If edit was not made (fallback to answer)
        edits = [c for c in bot.calls if type(c).__name__ == "SendMessage"]
    assert len(edits) >= 1
    text = str(edits[-1].text)
    assert "دانلود از یوتیوب" in text
    assert "تست ویدیوی یوتیوب" in text
    assert "creator_account" in text

    # Verify buttons
    markup = edits[-1].reply_markup
    assert markup is not None
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert any("dl:video:" in cb for cb in callbacks)
    assert any("dl:audio:" in cb for cb in callbacks)
    assert any("dl:cancel:" in cb for cb in callbacks)


async def test_downloader_auto_download_instagram(
    app_dispatcher: Dispatcher, session: AsyncSession, tmp_path: Path
) -> None:
    """Non-YouTube single media (Instagram) downloads automatically without asking."""
    bot = RecordingBot()
    fake_info = DownloadInfo(
        url="https://www.instagram.com/reel/C_test/",
        platform_key="instagram",
        platform_name="اینستاگرام",
        platform_icon="📱",
        title="تست ریلز جذاب",
        duration_seconds=30,
        duration_str="0:30",
        uploader="creator_account",
        thumbnail_url=None,
        is_video=True,
        is_audio_only=False,
        is_gallery=False,
    )

    dummy_video = tmp_path / "media.mp4"
    dummy_video.write_bytes(b"\x00\x00\x00\x18ftypisom" * 100)

    msg = Message(
        message_id=11,
        date=datetime.datetime.now(datetime.UTC),
        chat=Chat(id=USER_ID, type="private"),
        from_user=TgUser(id=USER_ID, is_bot=False, first_name="User"),
        text="https://www.instagram.com/reel/C_test/",
    )

    try:
        with (
            patch(
                "app.features.downloader.router.probe_media_url",
                new=AsyncMock(return_value=fake_info),
            ),
            patch(
                "app.features.downloader.router.download_video",
                new=AsyncMock(return_value=dummy_video),
            ),
        ):
            await app_dispatcher.feed_update(bot, Update(update_id=11, message=msg))
    finally:
        await bot.session.close()

    videos = [c for c in bot.calls if type(c).__name__ == "SendVideo"]
    assert len(videos) == 1
    assert "تست ریلز جذاب" in str(videos[0].caption)


async def test_downloader_cancel_flow(app_dispatcher: Dispatcher, session: AsyncSession) -> None:
    """Cancel button dismisses download prompt."""
    bot = RecordingBot()
    token = create_token()
    await store_download_context(token, {"url": "https://test.com"})

    cb = CallbackQuery(
        id="c_cancel",
        from_user=TgUser(id=USER_ID, is_bot=False, first_name="User"),
        message=Message(
            message_id=20,
            date=datetime.datetime.now(datetime.UTC),
            chat=Chat(id=USER_ID, type="private"),
            text="Menu",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        ),
        chat_instance="ci",
        data=DownloaderCB(action="cancel", token=token).pack(),
    )

    try:
        await app_dispatcher.feed_update(bot, Update(update_id=20, callback_query=cb))
    finally:
        await bot.session.close()

    edits = [c for c in bot.calls if type(c).__name__ in ("EditMessageText", "SendMessage")]
    assert len(edits) >= 1
    assert "دانلود لغو شد" in str(edits[-1].text)


async def test_downloader_execute_video(
    app_dispatcher: Dispatcher, session: AsyncSession, tmp_path: Path
) -> None:
    """Tapping video downloads and delivers the file."""
    bot = RecordingBot()
    token = create_token()
    await store_download_context(
        token,
        {
            "url": "https://youtube.com/watch?v=123",
            "platform_key": "youtube",
            "title": "ویدیوی تستی یوتیوب",
            "uploader": "Channel",
            "reply_to_id": 99,
        },
    )

    # Prepare dummy output file
    dummy_video = tmp_path / "media.mp4"
    dummy_video.write_bytes(b"\x00\x00\x00\x18ftypisom" * 100)

    cb = CallbackQuery(
        id="c_vid",
        from_user=TgUser(id=USER_ID, is_bot=False, first_name="User"),
        message=Message(
            message_id=30,
            date=datetime.datetime.now(datetime.UTC),
            chat=Chat(id=USER_ID, type="private"),
            text="Menu",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        ),
        chat_instance="ci",
        data=DownloaderCB(action="video", token=token).pack(),
    )

    try:
        with patch(
            "app.features.downloader.router.download_video", new=AsyncMock(return_value=dummy_video)
        ):
            await app_dispatcher.feed_update(bot, Update(update_id=30, callback_query=cb))
    finally:
        await bot.session.close()

    videos = [c for c in bot.calls if type(c).__name__ == "SendVideo"]
    assert len(videos) == 1
    assert "ویدیوی تستی یوتیوب" in str(videos[0].caption)

    # Conversion logged
    repo = ConversionRepo(session)
    count = await repo.count_successful()
    assert count >= 1


async def test_downloader_execute_audio(
    app_dispatcher: Dispatcher, session: AsyncSession, tmp_path: Path
) -> None:
    """Tapping audio extracts and delivers MP3."""
    bot = RecordingBot()
    token = create_token()
    await store_download_context(
        token,
        {
            "url": "https://soundcloud.com/test",
            "platform_key": "soundcloud",
            "title": "موزیک ساندکلاد",
            "uploader": "SoundArtist",
            "reply_to_id": 99,
        },
    )

    dummy_audio = tmp_path / "audio.mp3"
    dummy_audio.write_bytes(b"ID3\x04\x00\x00" * 50)

    cb = CallbackQuery(
        id="c_aud",
        from_user=TgUser(id=USER_ID, is_bot=False, first_name="User"),
        message=Message(
            message_id=40,
            date=datetime.datetime.now(datetime.UTC),
            chat=Chat(id=USER_ID, type="private"),
            text="Menu",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        ),
        chat_instance="ci",
        data=DownloaderCB(action="audio", token=token).pack(),
    )

    try:
        with patch(
            "app.features.downloader.router.download_audio", new=AsyncMock(return_value=dummy_audio)
        ):
            await app_dispatcher.feed_update(bot, Update(update_id=40, callback_query=cb))
    finally:
        await bot.session.close()

    audios = [c for c in bot.calls if type(c).__name__ == "SendAudio"]
    assert len(audios) == 1
    assert "موزیک ساندکلاد" in str(audios[0].caption)


async def test_downloader_respects_quota(app_dispatcher: Dispatcher, session: AsyncSession) -> None:
    """Downloader blocks execution when daily quota is exhausted."""
    from app.core.settings_store import get_settings_store

    await get_settings_store().set("daily_quota", "1")
    try:
        user_repo = UserRepo(session)
        await user_repo.upsert_from_telegram(
            TgUser(id=999_555_444, is_bot=False, first_name="QuotaUser")
        )
        conv = ConversionRepo(session)
        await conv.log_conversion(
            telegram_id=999_555_444, source_kind="url", target_id="dl_youtube", status="success"
        )
        await session.commit()

        token = create_token()
        await store_download_context(token, {"url": "https://test.com", "platform_key": "youtube"})

        bot = RecordingBot()
        cb = CallbackQuery(
            id="c_quota",
            from_user=TgUser(id=999_555_444, is_bot=False, first_name="QuotaUser"),
            message=Message(
                message_id=50,
                date=datetime.datetime.now(datetime.UTC),
                chat=Chat(id=999_555_444, type="private"),
                text="Menu",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            ),
            chat_instance="ci",
            data=DownloaderCB(action="video", token=token).pack(),
        )

        try:
            await app_dispatcher.feed_update(bot, Update(update_id=50, callback_query=cb))
        finally:
            await bot.session.close()

        # Quota alert displayed, no video sent
        videos = [c for c in bot.calls if type(c).__name__ == "SendVideo"]
        assert len(videos) == 0
        answers = [c for c in bot.calls if type(c).__name__ == "AnswerCallbackQuery"]
        assert len(answers) >= 1
        assert "سهمیه" in str(answers[0].text)
    finally:
        await get_settings_store().set("daily_quota", "0")
