"""Phase-8 acceptance tests: Voice & Audio Toolkit."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.media.audio_tools import (
    change_audio_speed,
    convert_to_mp3,
    convert_to_voice_note,
    extract_audio_from_video,
)

from tests.fixtures.gen import (
    generate_audio_fixture,
    generate_mp4_with_audio_fixture,
)

pytestmark = pytest.mark.media


async def test_convert_to_voice_note(tmp_path: Path) -> None:
    src_audio = generate_audio_fixture(tmp_path / "src.mp3", duration=2.0)

    out_voice = tmp_path / "voice.ogg"
    info = await convert_to_voice_note(src_audio, out_voice)

    assert out_voice.is_file()
    assert info.audio_codec in ("opus", "flac", "vorbis") or info.file_size > 0


async def test_convert_to_mp3(tmp_path: Path) -> None:
    src_audio = generate_audio_fixture(tmp_path / "src.mp3", duration=1.5)

    out_mp3 = tmp_path / "sound.mp3"
    info = await convert_to_mp3(src_audio, out_mp3, bitrate="128k")

    assert out_mp3.is_file()
    assert info.audio_codec == "mp3"


async def test_extract_audio_from_video(tmp_path: Path) -> None:
    src_mp4 = generate_mp4_with_audio_fixture(tmp_path / "vid.mp4", 640, 480, duration=2.0)

    out_audio = tmp_path / "extracted.mp3"
    info = await extract_audio_from_video(src_mp4, out_audio)

    assert out_audio.is_file()
    assert info.audio_codec == "mp3"


async def test_change_audio_speed(tmp_path: Path) -> None:
    src_audio = generate_audio_fixture(tmp_path / "src_spd.mp3", duration=3.0)

    out_fast = tmp_path / "fast.mp3"
    info = await change_audio_speed(src_audio, out_fast, speed_factor=1.5, to_voice=False)

    assert out_fast.is_file()
    assert info.audio_codec == "mp3"
