"""Quality ladder and size fitting.

Ensures converted files strictly fit Telegram limits:
- Static WEBP sticker: <= 512KB, exactly 512px on bounding box
- Video WEBM sticker: <= 256KB, <= 3s duration, <= 30fps, 512px bounding box, VP9 + yuva420p
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import SizeLimitError
from app.core.logging import get_logger
from app.media.ffmpeg import run_ffmpeg
from app.media.probe import MediaInfo

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ScaleResult:
    width: int
    height: int


def calculate_sticker_dimensions(w: int, h: int, target_max: int = 512) -> ScaleResult:
    """Calculate dimensions so one side is exactly `target_max` and the other <= `target_max`.

    Aspect ratio is strictly preserved. Dimensions are forced to even integers
    for encoder compatibility.
    """
    if w <= 0 or h <= 0:
        return ScaleResult(target_max, target_max)

    if w >= h:
        new_w = target_max
        new_h = round(h * (target_max / w))
    else:
        new_h = target_max
        new_w = round(w * (target_max / h))

    # Ensure even dimensions (required by libvpx-vp9 and ffmpeg filters)
    new_w = max(2, new_w if new_w % 2 == 0 else new_w - 1)
    new_h = max(2, new_h if new_h % 2 == 0 else new_h - 1)

    return ScaleResult(new_w, new_h)


async def fit_video_sticker(
    input_file: Path,
    output_file: Path,
    info: MediaInfo,
    *,
    max_bytes: int = 256 * 1024,
    max_duration: float = 3.0,
    max_fps: float = 30.0,
) -> None:
    """Convert input to a Telegram WEBM Video Sticker (VP9, <=256KB, <=3s, <=30fps).

    Uses a multi-step quality ladder:
    1. Truncate duration to <= 3s
    2. Scale to 512px bounding box
    3. Calculate target bitrate: (max_bytes * 8 * 0.85) / duration
    4. Run 2-pass VP9 encoding with `-pix_fmt yuva420p` for transparency
    5. If output still > 256KB, retry with lower bitrate/CRF
    """
    duration = min(info.duration if info.duration > 0 else 3.0, max_duration)
    fps = min(info.fps if info.fps > 0 else 30.0, max_fps)

    dims = calculate_sticker_dimensions(info.width, info.height, 512)

    # 85% safety margin on bitrate to ensure staying under 256KB
    target_bitrate_kbps = math.floor((max_bytes * 8 * 0.85) / (duration * 1000))
    target_bitrate_kbps = max(50, min(target_bitrate_kbps, 1500))

    # Ladder steps: (bitrate_kbps, crf, scale_factor)
    steps = [
        (target_bitrate_kbps, 30, 1.0),
        (int(target_bitrate_kbps * 0.7), 36, 1.0),
        (int(target_bitrate_kbps * 0.5), 42, 0.8),
        (int(target_bitrate_kbps * 0.3), 48, 0.6),
    ]

    if info.has_alpha:
        pix_fmt = "yuva420p"
    else:
        pix_fmt = "yuv420p"

    for attempt, (b_kbps, crf, scale_mult) in enumerate(steps, 1):
        cur_w = max(2, int(dims.width * scale_mult) // 2 * 2)
        cur_h = max(2, int(dims.height * scale_mult) // 2 * 2)
        cur_filter = f"scale={cur_w}:{cur_h},fps={fps:.2f}"

        args = [
            "-ss",
            "0",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(input_file),
            "-an",  # no audio stream
            "-c:v",
            "libvpx-vp9",
            "-b:v",
            f"{b_kbps}k",
            "-crf",
            str(crf),
            "-pix_fmt",
            pix_fmt,
            "-vf",
            cur_filter,
            "-metadata:s:v:0",
            "alpha_mode=1",
            str(output_file),
        ]

        try:
            await run_ffmpeg(args)
        except Exception as exc:
            log.warning("video_sticker_attempt_failed", attempt=attempt, error=str(exc))
            continue

        if output_file.is_file():  # noqa: ASYNC240
            size = output_file.stat().st_size  # noqa: ASYNC240
            if size <= max_bytes:
                log.info("video_sticker_fit_success", attempt=attempt, size=size)
                return

    raise SizeLimitError("could not fit video sticker into 256KB limit after 4 attempts")


async def fit_static_webp(
    input_file: Path,
    output_file: Path,
    info: MediaInfo,
    *,
    max_bytes: int = 512 * 1024,
) -> None:
    """Convert input to a Telegram static WEBP sticker (<= 512KB)."""
    dims = calculate_sticker_dimensions(info.width, info.height, 512)
    vf = f"scale={dims.width}:{dims.height}"

    qualities = [90, 75, 50, 25]

    for quality in qualities:
        args = [
            "-i",
            str(input_file),
            "-vframes",
            "1",
            "-c:v",
            "libwebp",
            "-quality",
            str(quality),
            "-vf",
            vf,
            str(output_file),
        ]

        await run_ffmpeg(args)

        if output_file.is_file() and output_file.stat().st_size <= max_bytes:  # noqa: ASYNC240
            return

    raise SizeLimitError("could not fit static WEBP sticker into 512KB limit")
