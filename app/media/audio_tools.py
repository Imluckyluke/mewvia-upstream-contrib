"""Audio tools: Voice Note conversion, MP3 conversion, audio extraction, and speed changing."""

from __future__ import annotations

from pathlib import Path

from app.core.errors import ConversionError
from app.core.logging import get_logger
from app.media.ffmpeg import run_ffmpeg
from app.media.probe import MediaInfo, probe

log = get_logger(__name__)


async def convert_to_voice_note(input_file: Path, output_file: Path) -> MediaInfo:
    """Convert any audio or video file to a Telegram Voice Note (OGG Opus)."""
    args = [
        "-i",
        str(input_file),
        "-vn",
        "-c:a",
        "libopus",
        "-b:a",
        "64k",
        "-vbr",
        "on",
        "-compression_level",
        "10",
        str(output_file),
    ]
    await run_ffmpeg(args)
    if not output_file.is_file():  # noqa: ASYNC240
        raise ConversionError("failed to generate voice note")
    return await probe(output_file)


async def convert_to_mp3(input_file: Path, output_file: Path, bitrate: str = "192k") -> MediaInfo:
    """Convert voice or audio file to MP3 audio file."""
    args = [
        "-i",
        str(input_file),
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        str(output_file),
    ]
    await run_ffmpeg(args)
    return await probe(output_file)


async def extract_audio_from_video(input_file: Path, output_file: Path) -> MediaInfo:
    """Extract audio stream from video file."""
    args = [
        "-i",
        str(input_file),
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(output_file),
    ]
    await run_ffmpeg(args)
    return await probe(output_file)


async def change_audio_speed(
    input_file: Path, output_file: Path, speed_factor: float = 1.5, to_voice: bool = True
) -> MediaInfo:
    """Change audio playback speed preserving pitch via ffmpeg atempo filter."""
    codec_args = (
        ["-c:a", "libopus", "-b:a", "64k"] if to_voice else ["-c:a", "libmp3lame", "-b:a", "192k"]
    )
    args = [
        "-i",
        str(input_file),
        "-vn",
        "-filter:a",
        f"atempo={speed_factor:.2f}",
        *codec_args,
        str(output_file),
    ]
    await run_ffmpeg(args)
    return await probe(output_file)
