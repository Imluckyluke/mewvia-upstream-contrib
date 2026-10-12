"""Photo and Image tools: resizing, conversion to PDF, frame extraction."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

from PIL import Image

from app.core.errors import ConversionError
from app.core.logging import get_logger
from app.media.ffmpeg import run_ffmpeg
from app.media.probe import MediaInfo, probe

log = get_logger(__name__)


async def resize_image(input_file: Path, output_file: Path, max_dimension: int = 1024) -> MediaInfo:
    """Resize image preserving aspect ratio up to max_dimension."""
    img: Any = Image.open(input_file)
    w, h = img.size
    resized_img: Any = img
    if w > max_dimension or h > max_dimension:
        if w >= h:
            new_w = max_dimension
            new_h = round(h * (max_dimension / w))
        else:
            new_h = max_dimension
            new_w = round(w * (max_dimension / h))
        resized_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    resized_img.save(output_file, format="PNG")
    img.close()

    return await probe(output_file)


async def images_to_pdf(input_files: list[Path], output_pdf: Path) -> Path:
    """Combine a list of image paths into a single PDF document."""
    if not input_files:
        raise ConversionError("no images provided to generate PDF")

    pil_images: list[Any] = []
    first_image: Any = None

    try:
        for f in input_files:
            img: Any = Image.open(f)
            if img.mode != "RGB":
                img = img.convert("RGB")
            if first_image is None:
                first_image = img
            else:
                pil_images.append(img)

        if first_image is None:
            raise ConversionError("failed to open images for PDF")

        first_image.save(output_pdf, format="PDF", save_all=True, append_images=pil_images)
        return output_pdf
    finally:
        if first_image:
            first_image.close()
        for img_obj in pil_images:
            img_obj.close()


async def extract_frames_to_zip(input_file: Path, output_zip: Path, work_dir: Path) -> Path:
    """Extract frames from GIF or video into a ZIP file."""
    frames_dir = work_dir / "extracted_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    args = [
        "-i",
        str(input_file),
        "-vf",
        "fps=5",  # 5 frames per second
        str(frames_dir / "frame_%04d.png"),
    ]
    await run_ffmpeg(args)

    frame_files = sorted(frames_dir.glob("frame_*.png"))
    if not frame_files:
        raise ConversionError("no frames extracted from file")

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for frame in frame_files:
            zf.write(frame, arcname=frame.name)

    return output_zip
