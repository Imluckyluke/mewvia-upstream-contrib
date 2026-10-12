"""Phase-7 acceptance tests: Video Note & Video Toolkit."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.media.probe import probe
from app.media.video_tools import compress_video, make_video_note, mute_video, reverse_video

from tests.fixtures.gen import generate_mp4_fixture

pytestmark = pytest.mark.media


async def test_make_video_note_crop_and_dimensions(tmp_path: Path) -> None:
    # 1280x720 landscape video -> 512x512 1:1 round video note
    src_mp4 = generate_mp4_fixture(tmp_path / "src_landscape.mp4", 1280, 720, duration=2.0)
    info = await probe(src_mp4)

    out_mp4 = tmp_path / "note_out.mp4"
    res_info = await make_video_note(src_mp4, out_mp4, info, max_side=512)

    assert out_mp4.is_file()
    assert res_info.width == 512
    assert res_info.height == 512
    assert res_info.aspect_ratio == 1.0
    assert res_info.video_codec == "h264"


async def test_compress_video_levels(tmp_path: Path) -> None:
    src_mp4 = generate_mp4_fixture(tmp_path / "src_hd.mp4", 1280, 720, duration=3.0)
    info = await probe(src_mp4)

    out_mp4 = tmp_path / "compressed.mp4"
    res_info = await compress_video(src_mp4, out_mp4, info, level="low")

    assert out_mp4.is_file()
    assert res_info.video_codec == "h264"


async def test_mute_video_removes_audio(tmp_path: Path) -> None:
    src_mp4 = generate_mp4_fixture(tmp_path / "src_audio.mp4", 640, 480, duration=2.0)

    out_mp4 = tmp_path / "muted.mp4"
    res_info = await mute_video(src_mp4, out_mp4)

    assert out_mp4.is_file()
    assert not res_info.has_audio


async def test_reverse_video_execution(tmp_path: Path) -> None:
    src_mp4 = generate_mp4_fixture(tmp_path / "src_rev.mp4", 400, 300, duration=1.5)
    info = await probe(src_mp4)

    out_mp4 = tmp_path / "reversed.mp4"
    res_info = await reverse_video(src_mp4, out_mp4, info)

    assert out_mp4.is_file()
    assert res_info.width == 400
    assert res_info.height == 300
