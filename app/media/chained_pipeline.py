"""Batch transformation pipeline: Wide -> Filter -> Text -> Encode.

Uses a unified FFmpeg filter_complex graph for videos/GIFs and a sequential
Pillow pipeline for images.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from app.core.logging import get_logger
from app.media.ffmpeg import run_ffmpeg
from app.media.filters import apply_pillow_filter
from app.media.fit import fit_static_webp, fit_video_sticker
from app.media.kinds import MediaKind
from app.media.pipeline import OutputFile, _convert_from_tgs
from app.media.probe import probe
from app.media.specs import (
    TARGET_GIF,
    TARGET_MP4,
    TARGET_PNG,
    TARGET_WEBM_STICKER,
    TARGET_WEBP,
    TargetSpec,
)
from app.media.text_overlay import render_text_overlay_image
from app.media.watermark import add_watermark_to_image, add_watermark_to_video

log = get_logger(__name__)


def _static_animation_args(src: Path, out: Path, duration: float) -> list[str]:
    """Encode a still image as a looping animation.

    Includes the even-dimension scale required by libx264 + yuv420p, so odd
    Pillow resize results (e.g. after the wide effect) cannot fail encoding.
    """
    return [
        "-loop",
        "1",
        "-i",
        str(src),
        "-t",
        str(duration),
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out),
    ]


async def _run_pillow_pipeline(
    src_path: Path,
    out_file: Path,
    target: TargetSpec,
    opts: dict[str, Any],
) -> None:
    """Run Pillow chain: Wide -> Filter -> Text overlay, then save."""
    with Image.open(src_path) as img_file:
        img: Image.Image = img_file.convert("RGBA")
        # 1. Wide
        if opts.get("wide"):
            orig_w, orig_h = img.size
            new_w = max(100, int(orig_w * 1.4))
            new_h = max(30, int(orig_h * 0.65))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # 2. Filter
        filter_id = opts.get("filter_id")
        if filter_id:
            img = apply_pillow_filter(img, filter_id)

        # 3. Text Overlay
        text_data = opts.get("text")
        if text_data:
            text = text_data.get("text", "")
            if text:
                w, h = img.size
                overlay_img = render_text_overlay_image(
                    canvas_w=w,
                    canvas_h=h,
                    text=text,
                    font_key=text_data.get("font", "vazir"),
                    color_key=text_data.get("color", "white"),
                    pos_key=text_data.get("pos", "bottom"),
                    size_key=text_data.get("size", "medium"),
                )
                img = Image.alpha_composite(img.convert("RGBA"), overlay_img)

        # Keep RGBA so transparent stickers survive the trip to fit_static_webp /
        # ffmpeg; discarding alpha here would paint a black background.
        img.save(out_file, "PNG")


async def _run_ffmpeg_pipeline(
    src_path: Path,
    out_file: Path,
    info: Any,
    target: TargetSpec,
    opts: dict[str, Any],
    work_dir: Path,
) -> None:
    """Build and run a unified FFmpeg filter_complex graph."""
    filters = []
    current_stream = "0:v"

    is_wide = opts.get("wide", False)
    filter_id = opts.get("filter_id")
    text_data = opts.get("text")

    # Base scale
    orig_w = info.width or 512
    orig_h = info.height or 512

    # 1. Wide
    if is_wide:
        new_w = max(100, int(orig_w * 1.4))
        new_h = max(30, int(orig_h * 0.65))
        if target.id == TARGET_WEBM_STICKER.id:
            max_side = max(new_w, new_h)
            scale = 512.0 / max_side
            target_w = max(2, int(new_w * scale) // 2 * 2)
            target_h = max(2, int(new_h * scale) // 2 * 2)
        else:
            target_w = max(2, new_w // 2 * 2)
            target_h = max(2, new_h // 2 * 2)
        filters.append(f"[{current_stream}]scale={target_w}:{target_h}[v1]")
        current_stream = "v1"
        orig_w, orig_h = target_w, target_h
    elif target.id == TARGET_GIF.id:
        filters.append(f"[{current_stream}]scale=trunc(iw/2)*2:trunc(ih/2)*2[v1]")
        current_stream = "v1"

    # 2. Filter
    if filter_id:
        vf = ""
        if filter_id == "bw":
            vf = "hue=s=0"
        elif filter_id == "negative":
            vf = "negate"
        elif filter_id == "sepia":
            vf = "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131:0"
        elif filter_id == "bright":
            vf = "eq=brightness=0.3:contrast=1.2:saturation=1.5"
        elif filter_id == "blur":
            vf = "boxblur=5:1"

        if vf:
            filters.append(f"[{current_stream}]{vf}[v2]")
            current_stream = "v2"

    # 3. Text Overlay
    args = ["-i", str(src_path)]
    if text_data and text_data.get("text"):
        text_png = work_dir / "text_overlay.png"
        overlay_img = render_text_overlay_image(
            canvas_w=orig_w,
            canvas_h=orig_h,
            text=text_data.get("text", ""),
            font_key=text_data.get("font", "vazir"),
            color_key=text_data.get("color", "white"),
            pos_key=text_data.get("pos", "bottom"),
            size_key=text_data.get("size", "medium"),
        )
        overlay_img.save(text_png, "PNG")
        args.extend(["-i", str(text_png)])
        filters.append(f"[{current_stream}][1:v]overlay=0:0[v3]")
        current_stream = "v3"

    # Assemble FFmpeg arguments
    if filters:
        args.extend(["-filter_complex", ";".join(filters)])
        args.extend(["-map", f"[{current_stream}]"])
    else:
        args.extend(["-map", "0:v"])

    # Keep audio if MP4 and not creating GIF/Sticker
    if info.has_audio and target.id == TARGET_MP4.id:
        args.extend(["-map", "0:a", "-c:a", "aac"])

    # Target specific configs
    if target.id == TARGET_WEBM_STICKER.id:
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
    elif target.id == TARGET_GIF.id:
        args.extend(
            [
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
            ]
        )
    elif target.id == TARGET_MP4.id:
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
    elif target.id == TARGET_PNG.id:
        # Single still frame out of a video stream.
        args.extend(["-vframes", "1"])

    args.append(str(out_file))
    await run_ffmpeg(args)


async def process_batch_media(
    src_path: Path,
    src_kind: MediaKind,
    target: TargetSpec,
    opts: dict[str, Any],
    work_dir: Path,
) -> OutputFile:
    """Execute chained operations based on batch options."""

    # Pre-process TGS animation to video first if needed
    if src_kind == MediaKind.STICKER_ANIMATED:
        # Temporary output target for TGS frames
        temp_out = work_dir / "tgs_temp.mp4"
        await _convert_from_tgs(src_path, TARGET_MP4, temp_out, work_dir)
        src_path = temp_out
        src_kind = MediaKind.VIDEO

    info = await probe(src_path)
    out_file = work_dir / f"batch_out.{target.extension}"

    is_video_engine = src_kind in (
        MediaKind.VIDEO,
        MediaKind.ANIMATION,
        MediaKind.VIDEO_NOTE,
        MediaKind.DOCUMENT_VIDEO,
        MediaKind.STICKER_VIDEO,
    )

    if is_video_engine and target.id == TARGET_WEBP.id:
        # Static sticker from a video: run the chain to a single still frame,
        # then fit it to Telegram's 512px / 512KB sticker limits.
        frame_png = work_dir / "frame.png"
        await _run_ffmpeg_pipeline(src_path, frame_png, info, TARGET_PNG, opts, work_dir)
        await fit_static_webp(frame_png, out_file, await probe(frame_png))
    elif is_video_engine:
        await _run_ffmpeg_pipeline(src_path, out_file, info, target, opts, work_dir)
    else:
        temp_png = work_dir / "temp_pillow.png"
        await _run_pillow_pipeline(src_path, temp_png, target, opts)

        if target.id == TARGET_WEBP.id:
            await fit_static_webp(temp_png, out_file, await probe(temp_png))
        elif target.id == TARGET_GIF.id:
            await run_ffmpeg(_static_animation_args(temp_png, out_file, 1.0))
        elif target.id == TARGET_MP4.id:
            await run_ffmpeg(_static_animation_args(temp_png, out_file, 3.0))
        elif target.id == TARGET_WEBM_STICKER.id:
            await fit_video_sticker(temp_png, out_file, await probe(temp_png))
        else:
            shutil.copy(temp_png, out_file)

    if target.id in (TARGET_PNG.id, TARGET_GIF.id, TARGET_MP4.id):
        try:
            await add_watermark_to_video(out_file, out_file) if target.id in (
                TARGET_GIF.id,
                TARGET_MP4.id,
            ) else await add_watermark_to_image(out_file, out_file)
        except Exception as exc:
            log.warning("batch_watermark_skipped", error=str(exc))

    out_info = await probe(out_file)
    return OutputFile(path=out_file, target=target, info=out_info)
