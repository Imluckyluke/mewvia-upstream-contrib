"""Tests verifying the 8 architectural and stability fixes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, Document, Message, Update
from aiogram.types import User as TgUser
from app.core.errors import ConversionError
from app.features.music_search.router import MusicSearchSG
from app.media.audio_tools import extract_audio_from_video
from app.media.kinds import MediaKind, detect_media_kind
from app.media.probe import MediaInfo
from app.ui.progress import ProgressController
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_sponsor import RecordingBot

USER_ID = 777_666_555


def _make_msg(text: str, user_id: int = USER_ID, **kwargs: object) -> Message:
    user = TgUser(id=user_id, is_bot=False, first_name="Auditor")
    chat = Chat(id=user_id, type="private")
    return Message(
        message_id=101,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text=text,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_detect_media_kind_identifies_audio_documents() -> None:
    """Issue 6: Audio files sent as uncompressed documents must be identified as MediaKind.AUDIO."""
    # 1. Document with audio/flac MIME
    doc_flac = Document(
        file_id="doc_flac_id",
        file_unique_id="uniq_flac",
        file_name="track.flac",
        mime_type="audio/flac",
        file_size=25_000_000,
    )
    msg_flac = _make_msg("", document=doc_flac)
    detected = detect_media_kind(msg_flac)
    assert detected is not None
    kind, _obj, f_id, f_size = detected
    assert kind == MediaKind.AUDIO
    assert f_id == "doc_flac_id"
    assert f_size == 25_000_000

    # 2. Document with generic octet-stream MIME but .mp3 filename
    doc_mp3 = Document(
        file_id="doc_mp3_id",
        file_unique_id="uniq_mp3",
        file_name="awesome_song.mp3",
        mime_type="application/octet-stream",
        file_size=8_000_000,
    )
    msg_mp3 = _make_msg("", document=doc_mp3)
    detected_mp3 = detect_media_kind(msg_mp3)
    assert detected_mp3 is not None
    assert detected_mp3[0] == MediaKind.AUDIO

    # 3. Non-audio document (e.g. PDF) remains unrecognized
    doc_pdf = Document(
        file_id="doc_pdf_id",
        file_unique_id="uniq_pdf",
        file_name="report.pdf",
        mime_type="application/pdf",
        file_size=1_000_000,
    )
    msg_pdf = _make_msg("", document=doc_pdf)
    assert detect_media_kind(msg_pdf) is None


async def test_silent_video_audio_extraction_raises_clean_error(tmp_path: Path) -> None:
    """Issue 8: Extracting audio from a silent video raises structured ConversionError."""
    dummy_in = tmp_path / "silent.mp4"
    dummy_out = tmp_path / "out.mp3"
    dummy_in.write_text("fake video data")

    silent_info = MediaInfo(
        path=dummy_in,
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

    with patch("app.media.audio_tools.probe", new_callable=AsyncMock) as mock_probe:
        mock_probe.return_value = silent_info

        with pytest.raises(ConversionError, match="این ویدیو فاقد هرگونه استریم صدا"):
            await extract_audio_from_video(dummy_in, dummy_out)


async def test_progress_controller_complete_with_text() -> None:
    """Issue 7: Completion text sets completed state and edits caption cleanly."""
    mock_bot = AsyncMock(spec=Bot)
    pc = ProgressController(
        bot=mock_bot,
        chat_id=12345,
        message_id=999,
        is_animation=True,
        stage_title="در حال پردازش…",
        auto_tick=False,
    )

    # Calling edit_text with a completion phrase
    await pc.edit_text("✅ دانلود آلبوم / پلی‌لیست به پایان رسید!")
    assert pc._completed is True
    mock_bot.edit_message_caption.assert_awaited_once()


async def test_cancel_command_clears_active_fsm_state(
    app_dispatcher: Dispatcher, session: AsyncSession
) -> None:
    """Issue 2: /cancel clears any active FSM state and presents the main menu."""
    bot = RecordingBot()
    storage = app_dispatcher.storage
    key = StorageKey(bot_id=bot.id, chat_id=USER_ID, user_id=USER_ID)
    state = FSMContext(storage=storage, key=key)

    try:
        # 1. Put user into an active state (e.g. searching for music)
        await state.set_state(MusicSearchSG.waiting_for_query)
        assert await state.get_state() == MusicSearchSG.waiting_for_query.state

        # 2. User sends /cancel
        update = Update(update_id=1, message=_make_msg("/cancel"))
        await app_dispatcher.feed_update(bot, update)

        # 3. State must be cleared!
        assert await state.get_state() is None

        # Verify confirmation was replied
        replies = [c for c in bot.calls if type(c).__name__ == "SendMessage"]
        assert len(replies) >= 1
        last_reply = replies[-1]
        assert "لغو شد" in str(getattr(last_reply, "text", ""))
    finally:
        await bot.session.close()


async def test_command_does_not_trigger_music_search_query(
    app_dispatcher: Dispatcher, session: AsyncSession
) -> None:
    """Issue 2: Slash commands in MusicSearchSG.waiting_for_query do not execute Spotify search."""
    bot = RecordingBot()
    storage = app_dispatcher.storage
    key = StorageKey(bot_id=bot.id, chat_id=USER_ID, user_id=USER_ID)
    state = FSMContext(storage=storage, key=key)

    try:
        await state.set_state(MusicSearchSG.waiting_for_query)

        with patch("app.features.music_search.router._execute_search") as mock_exec:
            # User types /start while in state
            update = Update(update_id=2, message=_make_msg("/start"))
            await app_dispatcher.feed_update(bot, update)

            # _execute_search must NOT be called with "/start"!
            mock_exec.assert_not_called()
    finally:
        await bot.session.close()
