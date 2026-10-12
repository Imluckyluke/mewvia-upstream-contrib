"""Unit and integration tests for vertical watermark engine."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.media.watermark import add_watermark_to_image, render_vertical_watermark_overlay
from PIL import Image


def test_render_vertical_watermark_overlay() -> None:
    overlay = render_vertical_watermark_overlay(512, 512, "@MewviaBot")
    assert isinstance(overlay, Image.Image)
    assert overlay.size == (512, 512)
    assert overlay.mode == "RGBA"


@pytest.mark.asyncio
async def test_add_watermark_to_image(tmp_path: Path) -> None:
    src = tmp_path / "input.png"
    out = tmp_path / "output.png"

    img = Image.new("RGB", (300, 300), color="blue")
    img.save(src, "PNG")

    res = await add_watermark_to_image(src, out, "@MewviaBot")
    assert res.is_file()
    assert res.stat().st_size > 0

    with Image.open(res) as out_img:
        assert out_img.size == (300, 300)
