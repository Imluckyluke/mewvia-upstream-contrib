"""Unit test for image to GIF conversion."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.media.kinds import MediaKind
from app.media.pipeline import convert
from app.media.specs import TARGET_GIF
from PIL import Image


@pytest.mark.asyncio
async def test_convert_photo_to_gif(tmp_path: Path) -> None:
    src_file = tmp_path / "test_image.png"
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    # Create dummy 300x200 photo
    img = Image.new("RGB", (300, 200), color="blue")
    img.save(src_file, "PNG")

    output = await convert(
        src_path=src_file,
        src_kind=MediaKind.PHOTO,
        target=TARGET_GIF,
        work_dir=work_dir,
    )

    assert output.path.is_file()
    assert output.path.stat().st_size > 0
    assert output.target == TARGET_GIF
    assert output.info.duration >= 1.0
