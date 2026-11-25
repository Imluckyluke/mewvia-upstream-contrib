"""Wide Media Engine (Squash height / Stretch width).

Creates the viral meme 'Wide' effect for photos, stickers, GIFs, and videos:
- Reduces vertical height by ~60% while expanding horizontal width.
- Preserves transparency for WEBP and WEBM stickers.
- Applies FFmpeg scale filter `scale=iw*1.8:ih*0.4` for videos/GIFs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image

from app.core.errors import ConversionError
from app.core.logging import get_logger
from app.media.ffmpeg import run_ffmpeg
from app.media.probe import probe
from app.media.watermark import add_watermark_to_image, add_watermark_to_video

log = get_logger(__name__)


async def make_image_wide(src_path: Path, out_path: Path) -> Path:
    await asyncio.to_thread(_resize_wide, src_path, out_path)
    await add_watermark_to_image(out_path, out_path)
    return out_path


def _resize_wide(src_path: Path, out_path: Path) -> None:
    """Make a static photo/WEBP image wide (balanced squashed height, stretched width)."""
    with Image.open(src_path) as img:
        orig_w, orig_h = img.size
        if orig_w * orig_h > 16_000_000:
            raise ConversionError("تعداد پیکسل‌های تصویر بیش از حد مجاز است.")
        # Balanced proportions: 1.4x width, 0.65x height (natural meme look)
        new_w = max(100, int(orig_w * 1.4))
        new_h = max(30, int(orig_h * 0.65))

        if out_path.suffix.lower() == ".webp":
            # Scale to Telegram sticker bounding box (max 512px) preserving wide ratio
            max_side = max(new_w, new_h)
            scale = 512.0 / max_side
            stk_w = max(2, int(new_w * scale) // 2 * 2)
            stk_h = max(2, int(new_h * scale) // 2 * 2)
            stk_img = img.resize((stk_w, stk_h), Image.Resampling.LANCZOS)
            stk_img.save(out_path, "WEBP", quality=85)
        else:
            img.resize((new_w, new_h), Image.Resampling.LANCZOS).save(out_path, "PNG")


async def make_video_wide(src_path: Path, out_path: Path) -> Path:
    """Make a video/GIF/WEBM sticker wide using FFmpeg scale filter."""
    info = await probe(src_path)
    orig_w = info.width or 512
    orig_h = info.height or 512

    new_w = max(100, int(orig_w * 1.4))
    new_h = max(30, int(orig_h * 0.65))

    is_webm = out_path.suffix.lower() == ".webm"

    if is_webm:
        # Scale to Telegram video sticker bounding box (max 512px)
        max_side = max(new_w, new_h)
        scale = 512.0 / max_side
        target_w = max(2, int(new_w * scale) // 2 * 2)
        target_h = max(2, int(new_h * scale) // 2 * 2)
        vf = f"scale={target_w}:{target_h}"
    else:
        target_w = max(2, new_w // 2 * 2)
        target_h = max(2, new_h // 2 * 2)
        vf = f"scale={target_w}:{target_h}"

    args = ["-i", str(src_path), "-vf", vf]

    if info.has_audio and not is_webm:
        args.extend(["-c:a", "copy"])

    if is_webm:
        duration = min(info.duration if info.duration > 0 else 3.0, 3.0)
        args.extend(
            [
                "-an",
                "-t",
                f"{duration:.3f}",
                "-c:v",
                "libvpx-vp9",
                "-b:v",
                "400k",
                "-crf",
                "32",
                "-pix_fmt",
                "yuva420p" if info.has_alpha else "yuv420p",
                "-metadata:s:v:0",
                "alpha_mode=1",
            ]
        )
    else:
        args.extend(
            [
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
            ]
        )

    args.append(str(out_path))
    await run_ffmpeg(args)

    await add_watermark_to_video(out_path, out_path)
    return out_path
