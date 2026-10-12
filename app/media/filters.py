"""Media visual filters engine.

Applies visual color/style filters to photos, stickers, GIFs, and videos:
- Grayscale (سیاه و سفید)
- Negative (نگاتیو / معکوس رنگ)
- Sepia (نوستالژیک / سپیا)
- Bright (درخشان / لایت)
- Blur (مات / بلور)

Uses Pillow for static images/WEBP stickers and FFmpeg vf filters for videos/GIFs/WEBM.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from app.core.logging import get_logger
from app.media.ffmpeg import run_ffmpeg
from app.media.probe import probe
from app.media.watermark import add_watermark_to_image, add_watermark_to_video

log = get_logger(__name__)


class FilterSpec(NamedTuple):
    id: str
    label: str
    emoji_slot: str
    ffmpeg_vf: str


FILTER_MAP: dict[str, FilterSpec] = {
    "bw": FilterSpec("bw", "سیاه و سفید", "flt_bw", "hue=s=0"),
    "negative": FilterSpec("negative", "نگاتیو", "flt_negative", "negate"),
    "sepia": FilterSpec(
        "sepia",
        "سپیا / نوستالژیک",
        "flt_sepia",
        "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
    ),
    "bright": FilterSpec("bright", "درخشان", "flt_bright", "eq=brightness=0.2:contrast=1.2"),
    "blur": FilterSpec("blur", "مات / بلور", "flt_blur", "boxblur=5:1"),
}


def apply_pillow_filter(img: Image.Image, filter_id: str) -> Image.Image:
    """Apply visual filter on a Pillow Image object preserving alpha."""
    has_alpha = img.mode in ("RGBA", "LA", "PA")
    alpha = img.split()[-1] if has_alpha else None

    # Convert to RGB for matrix operations
    rgb_img = img.convert("RGB")

    if filter_id == "bw":
        res = ImageOps.grayscale(rgb_img).convert("RGB")

    elif filter_id == "negative":
        res = ImageOps.invert(rgb_img)

    elif filter_id == "sepia":
        # Sepia transformation matrix
        sepia_matrix = (
            0.393,
            0.769,
            0.189,
            0,
            0.349,
            0.686,
            0.168,
            0,
            0.272,
            0.534,
            0.131,
            0,
        )
        res = rgb_img.convert("RGB", sepia_matrix)

    elif filter_id == "bright":
        enhancer = ImageEnhance.Brightness(rgb_img)
        res = enhancer.enhance(1.3)
        enhancer_c = ImageEnhance.Contrast(res)
        res = enhancer_c.enhance(1.1)

    elif filter_id == "blur":
        res = rgb_img.filter(ImageFilter.GaussianBlur(radius=5))

    else:
        res = rgb_img

    if has_alpha and alpha:
        res = res.convert("RGBA")
        res.putalpha(alpha)
    else:
        res = res.convert("RGB")

    return res


async def apply_filter_to_image(
    src_path: Path,
    out_path: Path,
    filter_id: str,
) -> Path:
    """Apply visual filter to static image/WEBP sticker."""
    with Image.open(src_path) as img:
        filtered = apply_pillow_filter(img, filter_id)

        if out_path.suffix.lower() == ".webp":
            # Scale to Telegram sticker bounding box (max 512px)
            max_side = max(filtered.width, filtered.height)
            scale = 512.0 / max_side
            stk_w = max(2, int(filtered.width * scale) // 2 * 2)
            stk_h = max(2, int(filtered.height * scale) // 2 * 2)
            stk_img = filtered.resize((stk_w, stk_h), Image.Resampling.LANCZOS)
            stk_img.save(out_path, "WEBP", quality=85)
        else:
            filtered.save(out_path, "PNG")

    await add_watermark_to_image(out_path, out_path)
    return out_path


async def apply_filter_to_video(
    src_path: Path,
    out_path: Path,
    filter_id: str,
) -> Path:
    """Apply visual filter to video/GIF/WEBM sticker using FFmpeg."""
    info = await probe(src_path)
    spec = FILTER_MAP.get(filter_id, FILTER_MAP["bw"])

    is_webm = out_path.suffix.lower() == ".webm"

    if is_webm:
        orig_w = info.width or 512
        orig_h = info.height or 512
        max_side = max(orig_w, orig_h)
        scale = 512.0 / max_side
        target_w = max(2, int(orig_w * scale) // 2 * 2)
        target_h = max(2, int(orig_h * scale) // 2 * 2)
        vf = f"{spec.ffmpeg_vf},scale={target_w}:{target_h}"
    else:
        vf = spec.ffmpeg_vf

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
