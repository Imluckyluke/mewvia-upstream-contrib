"""Persian text shaping and media overlay rendering engine.

Supports:
- Persian reshaping & bidi (RTL text joining).
- Customizable Persian TTF fonts (Vazirmatn, Lalezar, Samim, Sahel).
- High-visibility meme stroke outlines (default: white fill, black stroke).
- Position options (top, center, bottom).
- Static image overlay (Pillow) and video/GIF overlay (FFmpeg filter complex).
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import arabic_reshaper
from PIL import Image, ImageDraw, ImageFont

from app.core.logging import get_logger
from app.media.ffmpeg import run_ffmpeg
from app.media.probe import probe
from app.media.watermark import add_watermark_to_image, add_watermark_to_video

log = get_logger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"

DEFAULT_FONT = "vazir"

FONT_MAP: dict[str, str] = {
    "vazir": "Vazirmatn-Bold.ttf",
    "lalezar": "Lalezar-Regular.ttf",
    "samim": "Samim-Bold.ttf",
    "sahel": "Sahel-Bold.ttf",
}


class TextColorSpec(NamedTuple):
    fill: str
    stroke: str
    name: str
    emoji_slot: str


COLOR_MAP: dict[str, TextColorSpec] = {
    "white": TextColorSpec("#FFFFFF", "#000000", "سفید", "white_circle"),
    "yellow": TextColorSpec("#FFDF00", "#000000", "زرد", "yellow_circle"),
    "red": TextColorSpec("#FF3B30", "#000000", "قرمز", "red_circle"),
    "green": TextColorSpec("#34C759", "#000000", "سبز", "green_circle"),
    "blue": TextColorSpec("#007AFF", "#000000", "آبی", "blue_circle"),
    "black": TextColorSpec("#000000", "#FFFFFF", "مشکی", "black_square"),
}


SIZE_MULTIPLIERS: dict[str, float] = {
    "small": 0.05,
    "medium": 0.08,
    "large": 0.12,
    "xlarge": 0.16,
}


def reshape_persian(text: str) -> str:
    """Reshape Persian text for correct character joining."""
    if not text:
        return ""
    try:
        return arabic_reshaper.reshape(text)
    except Exception as exc:
        log.warning("persian_reshape_failed", text=text, error=str(exc))
        return text


def get_font_path(font_key: str) -> Path:
    filename = FONT_MAP.get(font_key, FONT_MAP[DEFAULT_FONT])
    font_path = ASSETS_DIR / filename
    if not font_path.is_file():
        # Fall back to default font if selected font file doesn't exist
        font_path = ASSETS_DIR / FONT_MAP[DEFAULT_FONT]
    return font_path


def load_font(font_key: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_path = get_font_path(font_key)
    if font_path.is_file():
        try:
            return ImageFont.truetype(str(font_path), size)
        except Exception:
            pass
    return ImageFont.load_default()


def calculate_optimal_font(
    text: str,
    font_key: str,
    max_w: int,
    max_h: int,
    size_key: str = "medium",
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
    """Determine font size and wrap text lines to fit within bounds."""
    lines = text.split("\n")
    multiplier = SIZE_MULTIPLIERS.get(size_key, 0.08)
    base_size = max(16, int(max_h * multiplier))

    for size in range(base_size, 11, -2):
        font = load_font(font_key, size)
        wrapped_lines = []

        for line in lines:
            reshaped_line = reshape_persian(line)
            bbox = font.getbbox(reshaped_line)
            w = bbox[2] - bbox[0]

            if w > max_w:
                words = line.split(" ")
                curr_line = ""
                for word in words:
                    test_line = f"{curr_line} {word}".strip()
                    reshaped_test = reshape_persian(test_line)
                    t_w = font.getbbox(reshaped_test)[2] - font.getbbox(reshaped_test)[0]
                    if t_w <= max_w:
                        curr_line = test_line
                    else:
                        if curr_line:
                            wrapped_lines.append(reshape_persian(curr_line))
                        curr_line = word
                if curr_line:
                    wrapped_lines.append(reshape_persian(curr_line))
            else:
                wrapped_lines.append(reshaped_line)

        total_h = len(wrapped_lines) * int(size * 1.25)
        if total_h <= max_h * 0.6:
            return font, wrapped_lines

    font = load_font(font_key, 14)
    reshaped_lines = [reshape_persian(line) for line in lines]
    return font, reshaped_lines


def render_text_overlay_image(
    canvas_w: int,
    canvas_h: int,
    text: str,
    font_key: str = "vazir",
    color_key: str = "white",
    pos_key: str = "bottom",
    size_key: str = "medium",
) -> Image.Image:
    """Create a transparent RGBA image of canvas dimensions with rendered text."""
    overlay = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font, reshaped_lines = calculate_optimal_font(
        text, font_key, int(canvas_w * 0.9), canvas_h, size_key
    )

    color_spec = COLOR_MAP.get(color_key, COLOR_MAP["white"])
    stroke_width = max(2, int(font.size * 0.08)) if hasattr(font, "size") else 2

    line_height = int(font.size * 1.25) if hasattr(font, "size") else 20
    total_text_h = len(reshaped_lines) * line_height

    # Calculate Y position
    if pos_key == "top":
        y_start = int(canvas_h * 0.05)
    elif pos_key == "center":
        y_start = int((canvas_h - total_text_h) / 2)
    else:  # bottom
        y_start = int(canvas_h - total_text_h - canvas_h * 0.08)

    y_start = max(10, y_start)

    for i, line in enumerate(reshaped_lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = int((canvas_w - text_w) / 2)
        y = y_start + i * line_height

        draw.text(
            (x, y),
            line,
            font=font,
            fill=color_spec.fill,
            stroke_width=stroke_width,
            stroke_fill=color_spec.stroke,
        )

    return overlay


async def add_text_to_image(
    src_path: Path,
    out_path: Path,
    text: str,
    font_key: str = "vazir",
    color_key: str = "white",
    pos_key: str = "bottom",
    size_key: str = "medium",
) -> Path:
    """Render text onto a static image/WEBP and save to out_path."""
    with Image.open(src_path) as img_file:
        img: Image.Image = img_file.convert("RGBA")
        overlay = render_text_overlay_image(
            img.width, img.height, text, font_key, color_key, pos_key, size_key
        )
        combined = Image.alpha_composite(img, overlay)

        # Save preserving format if webp/png
        if out_path.suffix.lower() == ".webp":
            combined.save(out_path, "WEBP")
        else:
            combined.save(out_path, "PNG")

    await add_watermark_to_image(out_path, out_path)
    return out_path


async def add_text_to_video(
    src_path: Path,
    out_path: Path,
    text: str,
    font_key: str = "vazir",
    color_key: str = "white",
    pos_key: str = "bottom",
    size_key: str = "medium",
) -> Path:
    """Render text overlay on a video/GIF via FFmpeg filter complex."""
    info = await probe(src_path)
    canvas_w = info.width or 512
    canvas_h = info.height or 512

    # Create temporary transparent overlay image
    overlay_path = out_path.parent / "text_overlay.png"
    overlay = render_text_overlay_image(
        canvas_w, canvas_h, text, font_key, color_key, pos_key, size_key
    )
    overlay.save(overlay_path, "PNG")

    # Use ffmpeg filter complex to overlay image over video
    args = [
        "-i",
        str(src_path),
        "-i",
        str(overlay_path),
        "-filter_complex",
        "[0:v][1:v]overlay=0:0",
    ]

    if info.has_audio:
        args.extend(["-c:a", "copy"])

    # Codec selection based on output format
    if out_path.suffix.lower() == ".webm":
        args.extend(
            [
                "-c:v",
                "libvpx-vp9",
                "-pix_fmt",
                "yuva420p",
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

    if overlay_path.is_file():
        overlay_path.unlink(missing_ok=True)

    await add_watermark_to_video(out_path, out_path)
    return out_path
