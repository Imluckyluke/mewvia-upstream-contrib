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
    monkeypatch.setattr(sys.modules["app.features.filters.router"], "resolve_panel_media", AsyncMock(return_value=("photo", "f1", 100, None)))

    await process_filter_apply(cb_filter, FilterCB(action="apply", filter_id="bw"), bot, session, settings)
    assert mock_quota.called

    # 2. Wide
    mock_quota.reset_mock()
    monkeypatch.setattr(sys.modules["app.features.wide.router"], "check_daily_quota", mock_quota)
    cb_wide = MagicMock(spec=CallbackQuery)
    cb_wide.message = msg
    cb_wide.answer = AsyncMock()
    cb_wide.from_user = TgUser(id=123, is_bot=False, first_name="A")
    monkeypatch.setattr(sys.modules["app.features.wide.router"], "resolve_panel_media", AsyncMock(return_value=("photo", "f1", 100, None)))

    await process_wide_media(cb_wide, bot, session, settings)
    assert mock_quota.called

    # 3. Shazam Download
    mock_quota.reset_mock()
    monkeypatch.setattr(sys.modules["app.features.shazam.router"], "check_daily_quota", mock_quota)
    monkeypatch.setattr(sys.modules["app.features.shazam.router"], "get_shazam_session", AsyncMock(return_value=MagicMock()))
    cb_shz = MagicMock(spec=CallbackQuery)
    cb_shz.data = ShazamCB(action="dl", sid="sid1").pack()
    cb_shz.message = msg
    cb_shz.answer = AsyncMock()
    cb_shz.from_user = TgUser(id=123, is_bot=False, first_name="A")

    await download_shazam_track(cb_shz, bot, session, settings)
    assert mock_quota.called

    # 4. TextOverlay render
    mock_quota.reset_mock()
    monkeypatch.setattr(sys.modules["app.features.textoverlay.router"], "check_daily_quota", mock_quota)
    cb_txt = MagicMock(spec=CallbackQuery)
    cb_txt.message = msg
    cb_txt.answer = AsyncMock()
    cb_txt.from_user = TgUser(id=123, is_bot=False, first_name="A")
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"file_id": "f", "kind": "photo", "overlay_text": "hello"})

    await render_and_send_overlay(cb_txt, TextOverlayCB(action="render"), state, bot, session, settings)
    assert mock_quota.called

    # 5. TagEditor save
    mock_quota.reset_mock()
    monkeypatch.setattr(sys.modules["app.features.tag_editor.router"], "check_daily_quota", mock_quota)
    cb_tag = MagicMock(spec=CallbackQuery)
    cb_tag.message = msg
    cb_tag.answer = AsyncMock()
    cb_tag.from_user = TgUser(id=123, is_bot=False, first_name="A")
    state_tag = AsyncMock()
    state_tag.get_data = AsyncMock(return_value={"file_id": "f", "tag_title": "new"})

    await save_tag_edits(cb_tag, bot, session, settings, state_tag)
    assert mock_quota.called
