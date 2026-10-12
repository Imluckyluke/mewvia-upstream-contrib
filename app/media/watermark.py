"""Vertical Watermark Engine.

Renders a subtle, semi-transparent vertical watermark tag (e.g. `@MewviaBot` or `@bot_username`)
rotated 90 degrees counter-clockwise along the left margin of photos, stickers, GIFs, and videos.

- Semi-transparent white text with dark outline for high contrast on any background.
- Compact vertical placement on the left side so it doesn't obstruct media content.
- Uses Pillow for static images/WEBP stickers and FFmpeg filter complex for videos/GIFs.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw, ImageFont

from app.config import get_settings
from app.core.logging import get_logger
from app.media.ffmpeg import run_ffmpeg
from app.media.probe import probe

log = get_logger(__name__)

ASSETS_FONTS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"


def render_vertical_watermark_overlay(
    canvas_w: int,
    canvas_h: int,
    watermark_text: str = "@MewviaBot",
) -> Image.Image:
    """Create a transparent RGBA overlay image with vertical text along the left edge."""
    overlay = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    if not watermark_text:
        return overlay

    text_to_draw = f"@{watermark_text.lstrip('@')}"

    # Determine font size relative to canvas height (~3.5% of height, min 10px, max 24px)
    font_size = max(10, min(int(canvas_h * 0.035), 24))

    font_path = ASSETS_FONTS_DIR / "Vazirmatn-Bold.ttf"
    try:
        font: ImageFont.FreeTypeFont = ImageFont.truetype(str(font_path), font_size)
    except Exception:
        font = cast(ImageFont.FreeTypeFont, ImageFont.load_default())

    # Measure text bounding box
    dummy_img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    d_draw = ImageDraw.Draw(dummy_img)
    bbox = d_draw.textbbox((0, 0), text_to_draw, font=font)
    t_w = int(bbox[2] - bbox[0])
    t_h = int(bbox[3] - bbox[1])

    # Create temporary horizontal text image with padding
    pad = 4
    txt_img = Image.new("RGBA", (t_w + pad * 2, t_h + pad * 2), (0, 0, 0, 0))
    txt_draw = ImageDraw.Draw(txt_img)

    # Semi-transparent text (140/255 opacity) with dark outline (120/255 opacity)
    stroke_w = max(1, font_size // 8)
    txt_draw.text(
        (pad, pad),
        text_to_draw,
        font=font,
        fill=(255, 255, 255, 140),
        stroke_width=stroke_w,
        stroke_fill=(0, 0, 0, 120),
    )

    # Rotate 90 degrees counter-clockwise for vertical orientation (bottom to top)
    rotated_txt = txt_img.rotate(90, expand=True)

    # Position at left margin, centered vertically
    _rot_w, rot_h = rotated_txt.size
    margin_x = max(2, int(canvas_w * 0.015))
    margin_y = max(10, (canvas_h - rot_h) // 2)

    overlay.alpha_composite(rotated_txt, (margin_x, margin_y))
    return overlay


async def add_watermark_to_image(
    src_path: Path,
    out_path: Path,
    watermark_text: str | None = None,
) -> Path:
    """Apply vertical watermark tag to static image / WEBP sticker."""
    if watermark_text is None:
        watermark_text = get_settings().bot_username

    with Image.open(src_path) as img_file:
        img: Image.Image = img_file.convert("RGBA")
        overlay = render_vertical_watermark_overlay(img.width, img.height, watermark_text)
        combined = Image.alpha_composite(img, overlay)

        if out_path.suffix.lower() == ".webp":
            combined.save(out_path, "WEBP", quality=90)
        else:
            combined.save(out_path, "PNG")

    return out_path


async def add_watermark_to_video(
    src_path: Path,
    out_path: Path,
    watermark_text: str | None = None,
) -> Path:
    """Apply vertical watermark tag to video / GIF / WEBM sticker via FFmpeg."""
    if watermark_text is None:
        watermark_text = get_settings().bot_username

    info = await probe(src_path)
    canvas_w = info.width or 512
    canvas_h = info.height or 512

    overlay = render_vertical_watermark_overlay(canvas_w, canvas_h, watermark_text)
    overlay_path = out_path.parent / "wm_overlay.png"
    overlay.save(overlay_path, "PNG")

    # If src_path == out_path, write to a temp file first
    # to prevent FFmpeg same-file read/write crash
    same_file = src_path.resolve() == out_path.resolve()  # noqa: ASYNC240
    target_out = out_path.parent / f"temp_wm_{out_path.name}" if same_file else out_path

    args = [
        "-i",
        str(src_path),
        "-i",
        str(overlay_path),
        "-filter_complex",
        "[0:v][1:v]overlay=0:0",
    ]

    is_webm = out_path.suffix.lower() == ".webm"
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

    args.append(str(target_out))
    await run_ffmpeg(args)

    if same_file and target_out.is_file():
        if out_path.is_file():  # noqa: ASYNC240
            try:
                out_path.unlink(missing_ok=True)  # noqa: ASYNC240
            except Exception:
                pass
        target_out.replace(out_path)

    if overlay_path.is_file():
        overlay_path.unlink(missing_ok=True)

    return out_path
