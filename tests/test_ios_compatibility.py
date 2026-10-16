from pathlib import Path
import subprocess
import pytest
from app.media.downloader import _ensure_ios_compatibility_sync
from app.media.probe import probe


@pytest.mark.asyncio
async def test_ensure_ios_compatibility_converts_vp9(tmp_path: Path):
    # Create a small dummy VP9 video
    vp9_path = tmp_path / "test_vp9.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1:size=320x240:rate=10",
        "-c:v",
        "libvpx-vp9",
        str(vp9_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    info_before = await probe(vp9_path)
    assert info_before.video_codec == "vp9"

    # Run iOS compatibility filter
    compat_path = _ensure_ios_compatibility_sync(vp9_path, tmp_path)
    assert compat_path.is_file()

    info_after = await probe(compat_path)
    assert info_after.video_codec == "h264"


@pytest.mark.asyncio
async def test_ensure_ios_compatibility_preserves_h264(tmp_path: Path):
    # Create a small standard H264 video
    h264_path = tmp_path / "test_h264.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1:size=320x240:rate=10",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(h264_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    info_before = await probe(h264_path)
    assert info_before.video_codec == "h264"

    # Run iOS compatibility filter
    compat_path = _ensure_ios_compatibility_sync(h264_path, tmp_path)
    assert compat_path.is_file()

    info_after = await probe(compat_path)
    assert info_after.video_codec == "h264"
