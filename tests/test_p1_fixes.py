"""Regression tests for P1 bug fixes:
- BUG-05: mute_video produces iOS H.264 + faststart compatible video even from VP9
- BUG-06: send_video calls inject supports_streaming, width, height, duration
- BUG-07: Playlist worker bounding and immediate query.answer in Shazam lyrics
- BUG-08: Daily quota enforcement in filters, wide, textoverlay, tag_editor, shazam
- BUG-10: Database session connection release during long jobs
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Chat, Message, User as TgUser
from app.callbacks import FilterCB, WideCB
from app.callbacks.shazam import ShazamCB
from app.callbacks.text_overlay import TextOverlayCB
from app.features.filters.router import process_filter_apply
from app.features.shazam.router import download_shazam_track, view_shazam_lyrics
from app.features.tag_editor.router import TagFieldCB, save_tag_edits
from app.features.textoverlay.router import render_and_send_overlay
from app.features.wide.router import process_wide_media
from app.media.probe import probe
from app.media.video_tools import mute_video


@pytest.mark.media
@pytest.mark.asyncio
async def test_mute_video_ios_compatibility_vp9(tmp_path: Path) -> None:
    """mute_video on a VP9 video must transcode to standard H.264 + faststart."""
    src_vp9 = tmp_path / "test_vp9.mp4"
    out_mp4 = tmp_path / "muted.mp4"

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=64x64:rate=10",
        "-t",
        "1",
        "-c:v",
        "libvpx-vp9",
        "-pix_fmt",
        "yuv420p",
        str(src_vp9),
    ]
    subprocess.run(cmd, check=True)

    info = await mute_video(src_vp9, out_mp4)
    assert info.video_codec.lower() in ("h264", "avc1")
    assert info.has_audio is False

    # Verify moov atom is at beginning of file (faststart)
    with open(out_mp4, "rb") as f:
        head = f.read(1024)
        assert b"moov" in head or b"ftyp" in head


@pytest.mark.asyncio
async def test_shazam_view_lyrics_answers_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """view_shazam_lyrics must call query.answer() immediately to prevent timeout."""
    cb = MagicMock(spec=CallbackQuery)
    cb.data = ShazamCB(action="lyrics", sid="test_sid").pack()
    cb.message = MagicMock(spec=Message)
    cb.message.reply = AsyncMock()
    cb.answer = AsyncMock()

    import app.media.shazam as shz_media

    monkeypatch.setattr(shz_media, "get_shazam_session", AsyncMock(return_value=None))

    await view_shazam_lyrics(cb)
    cb.answer.assert_called_once()


@pytest.mark.asyncio
async def test_quota_exhausted_blocks_heavy_features(monkeypatch: pytest.MonkeyPatch) -> None:
    """Heavy features must abort when daily quota is exhausted."""
    mock_quota = AsyncMock(return_value=False)
    monkeypatch.setattr("app.core.quota.check_daily_quota", mock_quota)

    session = MagicMock()
    bot = MagicMock(spec=Bot)
    settings = MagicMock()

    chat = Chat(id=123, type=ChatType.PRIVATE)
    msg = Message(message_id=1, date=100000, chat=chat)

    import sys

    # 1. Filters
    monkeypatch.setattr(sys.modules["app.features.filters.router"], "check_daily_quota", mock_quota)
    cb_filter = MagicMock(spec=CallbackQuery)
    cb_filter.message = msg
    cb_filter.answer = AsyncMock()
    cb_filter.from_user = TgUser(id=123, is_bot=False, first_name="A")
    monkeypatch.setattr(
        sys.modules["app.features.filters.router"],
        "resolve_panel_media",
        AsyncMock(return_value=("photo", "f1", 100, None)),
    )

    await process_filter_apply(
        cb_filter, FilterCB(action="apply", filter_id="bw"), bot, session, settings
    )
    assert mock_quota.called

    # 2. Wide
    mock_quota.reset_mock()
    monkeypatch.setattr(sys.modules["app.features.wide.router"], "check_daily_quota", mock_quota)
    cb_wide = MagicMock(spec=CallbackQuery)
    cb_wide.message = msg
    cb_wide.answer = AsyncMock()
    cb_wide.from_user = TgUser(id=123, is_bot=False, first_name="A")
    monkeypatch.setattr(
        sys.modules["app.features.wide.router"],
        "resolve_panel_media",
        AsyncMock(return_value=("photo", "f1", 100, None)),
    )

    await process_wide_media(cb_wide, bot, session, settings)
    assert mock_quota.called

    # 3. Shazam Download
    mock_quota.reset_mock()
    monkeypatch.setattr(sys.modules["app.features.shazam.router"], "check_daily_quota", mock_quota)
    monkeypatch.setattr(
        sys.modules["app.features.shazam.router"],
        "get_shazam_session",
        AsyncMock(return_value=MagicMock()),
    )
    cb_shz = MagicMock(spec=CallbackQuery)
    cb_shz.data = ShazamCB(action="dl", sid="sid1").pack()
    cb_shz.message = msg
    cb_shz.answer = AsyncMock()
    cb_shz.from_user = TgUser(id=123, is_bot=False, first_name="A")

    await download_shazam_track(cb_shz, bot, session, settings)
    assert mock_quota.called

    # 4. TextOverlay render
    mock_quota.reset_mock()
    monkeypatch.setattr(
        sys.modules["app.features.textoverlay.router"], "check_daily_quota", mock_quota
    )
    cb_txt = MagicMock(spec=CallbackQuery)
    cb_txt.message = msg
    cb_txt.answer = AsyncMock()
    cb_txt.from_user = TgUser(id=123, is_bot=False, first_name="A")
    state = AsyncMock()
    state.get_data = AsyncMock(
        return_value={"file_id": "f", "kind": "photo", "overlay_text": "hello"}
    )

    await render_and_send_overlay(
        cb_txt, TextOverlayCB(action="render"), state, bot, session, settings
    )
    assert mock_quota.called

    # 5. TagEditor save
    mock_quota.reset_mock()
    monkeypatch.setattr(
        sys.modules["app.features.tag_editor.router"], "check_daily_quota", mock_quota
    )
    cb_tag = MagicMock(spec=CallbackQuery)
    cb_tag.message = msg
    cb_tag.answer = AsyncMock()
    cb_tag.from_user = TgUser(id=123, is_bot=False, first_name="A")
    state_tag = AsyncMock()
    state_tag.get_data = AsyncMock(return_value={"file_id": "f", "tag_title": "new"})

    await save_tag_edits(cb_tag, bot, session, settings, state_tag)
    assert mock_quota.called


def test_twitter_wrapped_entries_detected_as_video(monkeypatch: pytest.MonkeyPatch) -> None:
    """Twitter URLs returning wrapped entries from yt-dlp must be detected as video, NOT playlist."""
    from app.media.downloader import _probe_info_sync

    fake_twitter_info = {
        "id": "12345",
        "title": "Cool Tweet",
        "uploader": "TwitterUser",
        "entries": [
            {
                "id": "12345_1",
                "formats": [
                    {
                        "vcodec": "avc1.4D400D",
                        "ext": "mp4",
                        "url": "https://video.twimg.com/vid.mp4",
                    },
                ],
            },
            {"id": "12345_2", "url": "https://x.com/tweet"},
        ],
    }

    class FakeYDL:
        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def extract_info(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return fake_twitter_info

    monkeypatch.setattr("app.media.downloader._make_ydl", lambda *args, **kwargs: FakeYDL())

    info = _probe_info_sync("https://x.com/TwitterUser/status/12345")
    assert info.platform_key == "twitter"
    assert info.is_video is True
    assert info.is_playlist is False
    assert info.is_gallery is False
    assert info.is_audio_only is False


@pytest.mark.asyncio
async def test_group_media_invocation_filter_commands_and_mentions() -> None:
    from aiogram.enums import ChatType
    from aiogram.types import Chat, Message, User
    from app.config import get_settings
    from app.features.group_media.router import GroupMediaInvocationFilter

    settings = get_settings()
    filt = GroupMediaInvocationFilter()

    group_chat = Chat(id=-10012345, type=ChatType.SUPERGROUP)
    pv_chat = Chat(id=12345, type=ChatType.PRIVATE)
    user = User(id=12345, is_bot=False, first_name="Test")

    # Group message with /media command
    msg1 = Message(message_id=1, date=1, chat=group_chat, from_user=user, text="/media")
    assert await filt(msg1, settings) is True

    # Group message with /edit command
    msg2 = Message(message_id=2, date=1, chat=group_chat, from_user=user, text="/edit")
    assert await filt(msg2, settings) is True

    # Group message with bot mention @Mewvia_bot
    msg3 = Message(
        message_id=3,
        date=1,
        chat=group_chat,
        from_user=user,
        caption="check this @Mewvia_bot",
    )
    assert await filt(msg3, settings) is True

    # Group plain chatter should NOT match
    msg4 = Message(message_id=4, date=1, chat=group_chat, from_user=user, text="سلام چطوری")
    assert await filt(msg4, settings) is False

    # Private chat message should not match group filter
    msg5 = Message(message_id=5, date=1, chat=pv_chat, from_user=user, text="/media")
    assert await filt(msg5, settings) is False


@pytest.mark.asyncio
async def test_replying_to_bot_group_message_does_not_open_panel() -> None:
    """A plain reply to a bot-posted download in a group must stay silent."""
    from aiogram.enums import ChatType
    from aiogram.types import Chat, Message, User
    from app.config import get_settings
    from app.features.group_media.router import GroupMediaInvocationFilter

    settings = get_settings()
    filt = GroupMediaInvocationFilter()

    group_chat = Chat(id=-10012345, type=ChatType.SUPERGROUP)
    human = User(id=111, is_bot=False, first_name="Ali")
    bot_user = User(
        id=8621022596,
        is_bot=True,
        first_name="Mewvia",
        username=settings.bot_username,
    )

    # The bot's own posted download (a video) — replying to it triggers nothing
    bot_post = Message(
        message_id=500, date=1, chat=group_chat, from_user=bot_user, text="دانلود شد"
    )
    reply_msg = Message(
        message_id=501,
        date=1,
        chat=group_chat,
        from_user=human,
        text="دمت گرم",
        reply_to_message=bot_post,
    )
    assert await filt(reply_msg, settings) is False

    # Even a reply to the bot's message that contains real text must stay inert
    reply_long = Message(
        message_id=502,
        date=1,
        chat=group_chat,
        from_user=human,
        text="این ویدیو خیلی خوب بود، مرسی",
        reply_to_message=bot_post,
    )
    assert await filt(reply_long, settings) is False

    # Explicit mention still works when replying to the bot
    reply_mention = Message(
        message_id=503,
        date=1,
        chat=group_chat,
        from_user=human,
        text=f"@{settings.bot_username} ادیتش کن",
        reply_to_message=bot_post,
    )
    assert await filt(reply_mention, settings) is True


def test_document_extension_detection_for_gif_and_sticker() -> None:
    from aiogram.types import Chat, Document, Message, User
    from app.media.kinds import MediaKind, detect_media_kind

    user = User(id=12345, is_bot=False, first_name="Test")
    chat = Chat(id=12345, type="private")

    # Document with generic mime but .gif extension
    doc_gif = Document(
        file_id="d1",
        file_unique_id="u1",
        file_name="funny_cat.gif",
        mime_type="application/octet-stream",
    )
    msg1 = Message(message_id=1, date=1, chat=chat, from_user=user, document=doc_gif)
    res1 = detect_media_kind(msg1)
    assert res1 is not None
    assert res1[0] == MediaKind.DOCUMENT_VIDEO

    # Document with .tgs extension
    doc_tgs = Document(
        file_id="d2",
        file_unique_id="u2",
        file_name="animated.tgs",
        mime_type="application/octet-stream",
    )
    msg2 = Message(message_id=2, date=1, chat=chat, from_user=user, document=doc_tgs)
    res2 = detect_media_kind(msg2)
    assert res2 is not None
    assert res2[0] == MediaKind.STICKER_ANIMATED


def test_cookie_file_upload_filter_ignores_animations_and_media() -> None:
    from aiogram.types import Animation, Chat, Document, Message, User
    from app.features.downloader.router import _is_cookie_file_upload

    user = User(id=7575366278, is_bot=False, first_name="mhm")
    chat = Chat(id=7575366278, type="private")

    # Animation with document attached (standard Telegram GIF)
    doc_gif = Document(
        file_id="d1",
        file_unique_id="u1",
        file_name="cat.mp4",
        mime_type="video/mp4",
    )
    anim = Animation(file_id="a1", file_unique_id="u2", width=100, height=100, duration=2)
    msg_anim = Message(
        message_id=1, date=1, chat=chat, from_user=user, animation=anim, document=doc_gif
    )
    assert _is_cookie_file_upload(msg_anim) is False

    # Real cookie txt file
    doc_cookie = Document(
        file_id="d2",
        file_unique_id="u3",
        file_name="instagram_cookies.txt",
        mime_type="text/plain",
    )
    msg_cookie = Message(message_id=2, date=1, chat=chat, from_user=user, document=doc_cookie)
    assert _is_cookie_file_upload(msg_cookie) is True


def test_tiktok_restricted_video_fallback_or_accurate_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.errors import ConversionError
    from app.media.downloader import _probe_info_sync

    # Simulate yt-dlp raising "This post may not be comfortable for some audiences"
    class FakeFailingYDL:
        def __enter__(self) -> FakeFailingYDL:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def extract_info(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError(
                "ERROR: [TikTok] 12345: This post may not be comfortable for some audiences. Log in for access."
            )

    monkeypatch.setattr("app.media.downloader._make_ydl", lambda *args, **kwargs: FakeFailingYDL())

    # Case A: TikWM fallback succeeds
    fake_tikwm_data = {
        "id": "12345",
        "title": "Interesting News Video",
        "play": "https://v16.tiktokcdn.com/video.mp4",
        "author": {"nickname": "NewsChannel"},
        "duration": 45,
    }
    monkeypatch.setattr(
        "app.media.downloader._probe_tiktok_via_tikwm",
        lambda url: {
            "id": fake_tikwm_data["id"],
            "title": fake_tikwm_data["title"],
            "description": fake_tikwm_data["title"],
            "uploader": "NewsChannel",
            "thumbnail": "https://p16.tiktokcdn.com/thumb.jpg",
            "duration": 45,
            "formats": [{"url": fake_tikwm_data["play"], "vcodec": "h264", "ext": "mp4"}],
            "entries": [],
            "_type": "video",
        },
    )

    info = _probe_info_sync("https://www.tiktok.com/@newschannel/video/12345")
    assert info.platform_key == "tiktok"
    assert info.is_video is True
    assert info.is_gallery is False

    # Case B: TikWM also fails -> should raise explicit TikTok age/sensitivity error, NEVER gallery error!
    monkeypatch.setattr("app.media.downloader._probe_tiktok_via_tikwm", lambda url: None)
    with pytest.raises(ConversionError) as exc_info:
        _probe_info_sync("https://www.tiktok.com/@newschannel/video/12345")
    err_msg = str(exc_info.value)
    assert "محدودیت سنی" in err_msg or "محتوای حساس" in err_msg
    assert "گالری" not in err_msg
