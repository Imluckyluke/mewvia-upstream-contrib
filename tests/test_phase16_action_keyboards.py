"""Tests for next-step interactive action keyboards on downloaded media."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message
from app.callbacks import AudioToolCB, FilterCB, TextOverlayCB, VideoToolCB, WideCB
from app.features.downloader.action_keyboards import (
    build_audio_actions_kb,
    build_photo_actions_kb,
    build_video_actions_kb,
)
from app.media.kinds import send_processing_status


class DummyRegistry:
    def text(self, slot: str) -> str:
        return f"[{slot}]"

    def custom_id(self, slot: str) -> str | None:
        return None

    def fallback(self, slot: str) -> str:
        return "🔹"


def test_build_video_actions_kb() -> None:
    reg = DummyRegistry()
    kb = build_video_actions_kb(reg)
    markup = kb.as_markup()
    buttons = [btn for row in markup.inline_keyboard for btn in row]

    callbacks = [b.callback_data for b in buttons if b.callback_data]
    assert VideoToolCB(action="note").pack() in callbacks
    assert VideoToolCB(action="extract").pack() in callbacks
    assert VideoToolCB(action="compress_menu").pack() in callbacks
    assert "vtool_action:start" in callbacks


def test_build_audio_actions_kb() -> None:
    reg = DummyRegistry()
    kb = build_audio_actions_kb(reg)
    markup = kb.as_markup()
    buttons = [btn for row in markup.inline_keyboard for btn in row]

    callbacks = [b.callback_data for b in buttons if b.callback_data]
    assert AudioToolCB(action="to_voice").pack() in callbacks
    assert AudioToolCB(action="speed_menu").pack() in callbacks
    assert AudioToolCB(action="tag_menu").pack() in callbacks


def test_build_photo_actions_kb() -> None:
    reg = DummyRegistry()
    kb = build_photo_actions_kb(reg)
    markup = kb.as_markup()
    buttons = [btn for row in markup.inline_keyboard for btn in row]

    callbacks = [b.callback_data for b in buttons if b.callback_data]
    assert WideCB(action="start").pack() in callbacks
    assert FilterCB(action="start").pack() in callbacks
    assert "spack_action:start" in callbacks
    assert TextOverlayCB(action="start").pack() in callbacks


@pytest.mark.asyncio
async def test_send_processing_status_on_text_message() -> None:
    msg = MagicMock(spec=Message)
    msg.text = "Hello"
    msg.edit_text = AsyncMock(return_value=msg)

    res = await send_processing_status(msg, "Processing...")
    assert res == msg
    msg.edit_text.assert_awaited_once_with("Processing...", reply_markup=None)


@pytest.mark.asyncio
async def test_send_processing_status_on_media_message() -> None:
    msg = MagicMock(spec=Message)
    msg.text = None
    msg.video = MagicMock()
    reply_msg = MagicMock(spec=Message)
    msg.reply = AsyncMock(return_value=reply_msg)

    res = await send_processing_status(msg, "Processing...")
    assert res == reply_msg
    msg.reply.assert_awaited_once_with("Processing...")
