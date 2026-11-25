"""Video tools: Video Note generation, compression, muting, and reversing."""

from __future__ import annotations

from pathlib import Path

from app.core.errors import ConversionError
from app.core.logging import get_logger
from app.media.ffmpeg import run_ffmpeg
from app.media.probe import MediaInfo, probe

log = get_logger(__name__)


async def make_video_note(
    input_file: Path, output_file: Path, info: MediaInfo, max_side: int = 512
) -> MediaInfo:
    """Convert any video to a square 1:1 round video note (max 512x512, H.264/AAC)."""
    # Crop to 1:1 square centered
    w, h = info.width, info.height
    min_dim = min(w, h)
    crop_filter = (
        f"crop={min_dim}:{min_dim}:(iw-{min_dim})/2:(ih-{min_dim})/2,scale={max_side}:{max_side}"
    )

    args = [
        "-i",
        str(input_file),
        "-t",
        "60",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "26",
        "-vf",
        crop_filter,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        str(output_file),
    ]

    # If input has no audio, construct without audio codec
    if not info.has_audio:
        args = [
            "-i",
            str(input_file),
            "-t",
            "60",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "26",
            "-vf",
            crop_filter,
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_file),
        ]

    await run_ffmpeg(args)
    if not output_file.is_file():  # noqa: ASYNC240
        raise ConversionError("failed to generate video note")

    return await probe(output_file)


async def compress_video(
    input_file: Path, output_file: Path, info: MediaInfo, level: str = "medium"
) -> MediaInfo:
    """Compress video with quality levels: low (CRF 32), medium (CRF 26), high (CRF 22)."""
    crf_map = {"high": "22", "medium": "26", "low": "32"}
    crf = crf_map.get(level, "26")

    # If width > 1280, scale down to 1280 to save size
    vf_arg: list[str] = []
    if info.width > 1280 or info.height > 1280:
        vf_arg = [
            "-vf",
            "scale='min(1280,iw)':'min(1280,ih)':force_original_aspect_ratio=decrease,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        ]

    args = [
        "-i",
        str(input_file),
        "-c:v",
        "libx264",
        "-crf",
        crf,
        "-preset",
        "veryfast",
        *vf_arg,
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]

    if info.has_audio:
        args.extend(["-c:a", "aac", "-b:a", "128k"])
    else:
        args.append("-an")

    args.append(str(output_file))

    await run_ffmpeg(args)
    return await probe(output_file)


async def mute_video(input_file: Path, output_file: Path) -> MediaInfo:
    """Remove audio stream from video with iOS H.264/HEVC and faststart compatibility."""
    info = await probe(input_file)
    if info.video_codec.lower() in ("h264", "hevc") and not info.has_alpha:
        args = [
            "-i",
            str(input_file),
            "-an",
            "-c:v",
            "copy",
            "-movflags",
            "+faststart",
            str(output_file),
        ]
    else:
        args = [
            "-i",
            str(input_file),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_file),
        ]
    await run_ffmpeg(args)
    return await probe(output_file)


async def reverse_video(input_file: Path, output_file: Path, info: MediaInfo) -> MediaInfo:
    """Reverse video (and audio if present)."""
    if info.duration and info.duration > 20.0:
        raise ConversionError(
            "معکوس کردن ویدیو فقط برای ویدیوهای تا ۲۰ ثانیه امکان‌پذیر است تا حافظه سرور اشباع نشود."
        )

    vf_args = [
        "-vf",
        "fps=15,scale='min(iw,640)':'min(ih,480)':force_original_aspect_ratio=decrease:"
        "force_divisible_by=2,format=yuv420p,reverse",
    ]
    vf_args += (
        ["-af", "aresample=44100,aformat=channel_layouts=stereo,areverse"]
        if info.has_audio
        else ["-an"]
    )

    args = [
        "-t",
        "20",
        "-i",
        str(input_file),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-threads",
        "2",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        *vf_args,
        str(output_file),
    ]

    await run_ffmpeg(args)
    return await probe(output_file)
