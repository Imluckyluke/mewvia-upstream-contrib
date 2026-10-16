"""Tests for the Shazam music identification feature."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, Voice
from aiogram.types import User as TgUser
from app.callbacks.shazam import ShazamCB
from app.core.errors import ConversionError
from app.media.probe import MediaInfo
from app.media.shazam import (
    ShazamTrack,
    build_shazam_keyboard,
    create_shazam_session_id,
    format_shazam_caption,
    get_shazam_session,
    identify_music_from_file,
    store_shazam_session,
)
from app.ui.emoji import EmojiRegistry
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_sponsor import RecordingBot

USER_ID = 555_444_333


def _make_msg(text: str = "", user_id: int = USER_ID, **kwargs: object) -> Message:
    user = TgUser(id=user_id, is_bot=False, first_name="ShazamUser")
    chat = Chat(id=user_id, type="private")
    return Message(
        message_id=200,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text=text,
        **kwargs,  # type: ignore[arg-type]
    )


def test_shazam_track_serialization() -> None:
    track = ShazamTrack(
        title="Numb",
        artist="Linkin Park",
        album="Meteora",
        genres=("Alternative", "Rock"),
        release_year="2003",
        cover_url="https://example.com/cover.jpg",
        shazam_url="https://shazam.com/track/123",
        apple_music_url="https://music.apple.com/track/123",
    )
    d = track.to_dict()
    assert d["title"] == "Numb"
    assert d["artist"] == "Linkin Park"
    assert d["album"] == "Meteora"
    assert d["genres"] == ["Alternative", "Rock"]

    rebuilt = ShazamTrack.from_dict(d)
    assert rebuilt == track


async def test_shazam_session_storage() -> None:
    sid = create_shazam_session_id()
    track = ShazamTrack(
        title="Yellow",
        artist="Coldplay",
        album="Parachutes",
        release_year="2000",
    )
    await store_shazam_session(sid, track)

    retrieved = await get_shazam_session(sid)
    assert retrieved is not None
    assert retrieved.title == "Yellow"
    assert retrieved.artist == "Coldplay"
    assert retrieved.album == "Parachutes"


async def test_identify_music_silent_file_raises_conversion_error(tmp_path: Path) -> None:
    silent_file = tmp_path / "silent.mp4"
    silent_file.write_text("fake video data")

    silent_info = MediaInfo(
        path=silent_file,
        width=1280,
        height=720,
        duration=10.0,
        fps=30.0,
        video_codec="h264",
        audio_codec=None,
        has_alpha=False,
        has_audio=False,
        file_size=1024,
    )

    with patch("app.media.shazam.probe", new_callable=AsyncMock) as mock_probe:
        mock_probe.return_value = silent_info
        with pytest.raises(ConversionError, match="فاقد هرگونه استریم صدا"):
            await identify_music_from_file(silent_file, tmp_path)


async def test_identify_music_successful_recognition(tmp_path: Path) -> None:
    audio_file = tmp_path / "voice.ogg"
    audio_file.write_text("fake audio")

    audio_info = MediaInfo(
        path=audio_file,
        width=0,
        height=0,
        duration=15.0,
        fps=0.0,
        video_codec="",
        audio_codec="opus",
        has_alpha=False,
        has_audio=True,
        file_size=5000,
    )

    fake_shazam_resp = {
        "track": {
            "title": "Kojaei",
            "subtitle": "Mohsen Chavoshi",
            "url": "https://www.shazam.com/track/123/kojaei",
            "genres": {"primary": "Pop"},
            "images": {
                "coverart": "https://example.com/chavoshi.jpg",
                "coverarthq": "https://example.com/chavoshi_hq.jpg",
            },
            "sections": [
                {
                    "type": "SONG",
                    "metadata": [
                        {"title": "Album", "text": "Shahrzad Soundtrack"},
                        {"title": "Released", "text": "2015"},
                    ],
                }
            ],
            "hub": {
                "actions": [
                    {"type": "applemusicplay", "uri": "https://music.apple.com/album/kojaei"}
                ]
            },
        }
    }

    with (
        patch("app.media.shazam.probe", new_callable=AsyncMock) as mock_probe,
        patch("app.media.shazam.run_ffmpeg", new_callable=AsyncMock) as mock_ffmpeg,
        patch("app.media.shazam.Shazam") as mock_shazam_cls,
    ):
        mock_probe.return_value = audio_info

        # Create dummy sample file that prepare_shazam_audio would produce
        def fake_ffmpeg(args: list[str]) -> None:
            out_file = Path(args[-1])
            out_file.write_text("mp3 sample data")

        mock_ffmpeg.side_effect = fake_ffmpeg

        mock_shazam_inst = MagicMock()
        mock_shazam_inst.recognize = AsyncMock(return_value=fake_shazam_resp)
        mock_shazam_cls.return_value = mock_shazam_inst

        result = await identify_music_from_file(audio_file, tmp_path)
        assert result is not None
        assert result.title == "Kojaei"
        assert result.artist == "Mohsen Chavoshi"
        assert result.album == "Shahrzad Soundtrack"
        assert result.release_year == "2015"
        assert result.genres == ("Pop",)
        assert result.cover_url == "https://example.com/chavoshi_hq.jpg"
        assert result.shazam_url == "https://www.shazam.com/track/123/kojaei"
        assert result.apple_music_url == "https://music.apple.com/album/kojaei"


async def test_identify_music_no_match_returns_none(tmp_path: Path) -> None:
    audio_file = tmp_path / "noise.mp3"
    audio_file.write_text("fake noise")

    audio_info = MediaInfo(
        path=audio_file,
        width=0,
        height=0,
        duration=5.0,
        fps=0.0,
        video_codec="",
        audio_codec="mp3",
        has_alpha=False,
        has_audio=True,
        file_size=2000,
    )

    with (
        patch("app.media.shazam.probe", new_callable=AsyncMock) as mock_probe,
        patch("app.media.shazam.run_ffmpeg", new_callable=AsyncMock) as mock_ffmpeg,
        patch("app.media.shazam.Shazam") as mock_shazam_cls,
    ):
        mock_probe.return_value = audio_info

        def fake_ffmpeg(args: list[str]) -> None:
            Path(args[-1]).write_text("sample")

        mock_ffmpeg.side_effect = fake_ffmpeg

        mock_shazam_inst = MagicMock()
        mock_shazam_inst.recognize = AsyncMock(return_value={"matches": []})
        mock_shazam_cls.return_value = mock_shazam_inst

        result = await identify_music_from_file(audio_file, tmp_path)
        assert result is None


def test_shazam_card_formatting_and_keyboard() -> None:
    registry = EmojiRegistry()
    track = ShazamTrack(
        title="Starboy",
        artist="The Weeknd",
        album="Starboy",
        genres=("R&B",),
        release_year="2016",
        shazam_url="https://shazam.com/track/starboy",
    )

    caption = format_shazam_caption(track, registry, "MewviaBot")
    assert "Starboy" in caption
    assert "The Weeknd" in caption
    assert "شناسایی شده با @MewviaBot" in caption

    kb = build_shazam_keyboard("test_sid", track, registry)
    markup = kb.as_markup()
    # Row 1: Download
    assert any("دانلود" in str(b.text) for row in markup.inline_keyboard for b in row)
    # Row 2: Lyrics
    assert any("متن ترانه" in str(b.text) for row in markup.inline_keyboard for b in row)
    # Row 3: Shazam link
    assert any(
        b.url == "https://shazam.com/track/starboy" for row in markup.inline_keyboard for b in row
    )


async def test_cmd_shazam_without_media_returns_guide(
    app_dispatcher: Dispatcher, session: AsyncSession
) -> None:
    bot = RecordingBot()
    try:
        update = Update(update_id=1, message=_make_msg("/shazam"))
        await app_dispatcher.feed_update(bot, update)

        replies = [c for c in bot.calls if type(c).__name__ == "SendMessage"]
        assert len(replies) >= 1
        assert "شناسایی آهنگ با Shazam" in str(getattr(replies[-1], "text", ""))
    finally:
        await bot.session.close()


async def test_cmd_shazam_with_reply_triggers_flow(
    app_dispatcher: Dispatcher, session: AsyncSession
) -> None:
    bot = RecordingBot()
    try:
        voice = Voice(
            file_id="voice_file_123",
            file_unique_id="uniq_v_123",
            duration=8,
            file_size=24000,
        )
        replied_msg = _make_msg("", voice=voice)

        cmd_msg = _make_msg("/shazam", reply_to_message=replied_msg)
        update = Update(update_id=2, message=cmd_msg)

        with patch("app.features.shazam.router.execute_shazam_flow") as mock_flow:
            await app_dispatcher.feed_update(bot, update)
            mock_flow.assert_awaited_once()
            args, _kwargs = mock_flow.call_args
            assert args[1] == "voice_file_123"
    finally:
        await bot.session.close()


async def test_shazam_callback_close() -> None:
    from app.features.shazam.router import close_shazam_card

    mock_query = MagicMock()
    mock_msg = AsyncMock(spec=Message)
    mock_query.message = mock_msg
    mock_query.answer = AsyncMock()

    await close_shazam_card(mock_query)
    mock_msg.delete.assert_called_once()
    mock_query.answer.assert_called_once()


async def test_shazam_callback_lyrics() -> None:
    from app.features.shazam.router import view_shazam_lyrics

    sid = create_shazam_session_id()
    track = ShazamTrack(title="Hello", artist="Adele")
    await store_shazam_session(sid, track)

    mock_query = MagicMock()
    mock_query.data = ShazamCB(action="lyrics", sid=sid).pack()
    mock_msg = MagicMock(spec=Message)
    mock_msg.reply = AsyncMock()
    mock_query.message = mock_msg
    mock_query.answer = AsyncMock()

    with patch("app.features.shazam.router.fetch_lyrics", new_callable=AsyncMock) as mock_lyrics:
        mock_lyrics.return_value = "Hello, it's me\nI was wondering..."
        await view_shazam_lyrics(mock_query)
        mock_msg.reply.assert_awaited_once()
        sent_text = str(mock_msg.reply.call_args[0][0])
        assert "Hello, it's me" in sent_text


async def test_shazam_callback_download(tmp_path: Path) -> None:
    from app.features.shazam.router import download_shazam_track

    sid = create_shazam_session_id()
    track = ShazamTrack(title="Yellow", artist="Coldplay")
    await store_shazam_session(sid, track)

    mock_query = MagicMock()
    mock_query.data = ShazamCB(action="dl", sid=sid).pack()
    mock_msg = MagicMock(spec=Message)
    mock_msg.chat = Chat(id=12345, type="private")
    mock_msg.message_id = 99
    mock_msg.reply_to_message = None
    mock_query.message = mock_msg
    mock_query.answer = AsyncMock()
    mock_query.from_user.id = USER_ID

    fake_mp3 = tmp_path / "song.mp3"
    fake_mp3.write_text("mp3-data")

    mock_bot = AsyncMock(spec=Bot)
    mock_session = AsyncMock(spec=AsyncSession)
    mock_settings = MagicMock(
        upload_limit=50_000_000,
        bot_username="MewviaBot",
        tmp_dir=tmp_path,
        max_concurrent_jobs=4,
        job_timeout=60,
    )

    with (
        patch(
            "app.features.shazam.router.ProgressController.start", new_callable=AsyncMock
        ) as mock_pc_cls,
        patch(
            "app.features.shazam.router.download_spotify_track", new_callable=AsyncMock
        ) as mock_dl,
        patch("app.features.shazam.router.fetch_lyrics", new_callable=AsyncMock) as mock_lyrics,
        patch("app.features.shazam.router.ConversionRepo.log_conversion", new_callable=AsyncMock),
    ):
        mock_pc = AsyncMock()
        mock_pc_cls.return_value = mock_pc
        mock_dl.return_value = fake_mp3
        mock_lyrics.return_value = None

        await download_shazam_track(mock_query, mock_bot, mock_session, mock_settings)
        mock_bot.send_audio.assert_awaited_once()
        audio_kwargs = mock_bot.send_audio.call_args[1]
        assert "Yellow" in audio_kwargs["caption"]
        assert "Coldplay" in audio_kwargs["caption"]


async def test_progress_controller_stops_ticker_on_warning_notice() -> None:
    """Ensure warning/unidentified notices cleanly cancel the ticker and do not keep progress bar."""
    from app.ui.progress import ProgressController

    mock_bot = AsyncMock(spec=Bot)
    pc = ProgressController(
        bot=mock_bot,
        chat_id=12345,
        message_id=999,
        is_animation=True,
        stage_title="در حال پردازش…",
        auto_tick=True,
    )

    err_msg = "⚠️ متأسفانه آهنگی شناسایی نشد.\n\nلطفاً مطمئن شوید بخش انتخابی دارای موزیک واضح است."
    await pc.edit_text(err_msg)

    assert pc._completed is True
    # Animation must be deleted and error sent cleanly
    mock_bot.delete_message.assert_awaited_once_with(12345, 999)
    mock_bot.send_message.assert_awaited_once_with(12345, err_msg, parse_mode="HTML")
