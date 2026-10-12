"""Unit and integration tests for Persian text overlay engine and feature."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.features import load_features
from app.media.text_overlay import (
    COLOR_MAP,
    FONT_MAP,
    add_text_to_image,
    render_text_overlay_image,
    reshape_persian,
)
from PIL import Image


def test_persian_reshape() -> None:
    raw_text = "سلام دنیا"
    reshaped = reshape_persian(raw_text)
    assert reshaped != ""
    assert isinstance(reshaped, str)


def test_fonts_and_colors_exist() -> None:
    assert "vazir" in FONT_MAP
    assert "lalezar" in FONT_MAP
    assert "white" in COLOR_MAP
    assert "yellow" in COLOR_MAP


def test_render_text_overlay_image() -> None:
    overlay = render_text_overlay_image(
        canvas_w=512,
        canvas_h=512,
        text="بوبولم تعجب",
        font_key="vazir",
        color_key="white",
        pos_key="bottom",
    )
    assert isinstance(overlay, Image.Image)
    assert overlay.size == (512, 512)
    assert overlay.mode == "RGBA"


@pytest.mark.asyncio
async def test_add_text_to_image(tmp_path: Path) -> None:
    src = tmp_path / "input.png"
    out = tmp_path / "output.png"

    # Create dummy 200x200 image
    img = Image.new("RGB", (200, 200), color="blue")
    img.save(src, "PNG")

    res = await add_text_to_image(
        src_path=src,
        out_path=out,
        text="تست متن فارسی",
        font_key="vazir",
        color_key="yellow",
        pos_key="top",
    )

    assert res.is_file()
    assert res.stat().st_size > 0

    with Image.open(res) as out_img:
        assert out_img.size == (200, 200)


def test_textoverlay_feature_loaded() -> None:
    features = load_features()
    names = [f.name for f in features]
    assert "textoverlay" in names
