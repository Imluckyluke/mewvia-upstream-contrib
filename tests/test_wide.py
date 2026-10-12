"""Unit and integration tests for Wide media engine and feature."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.features import load_features
from app.media.wide import make_image_wide
from PIL import Image


@pytest.mark.asyncio
async def test_make_image_wide(tmp_path: Path) -> None:
    src = tmp_path / "input.png"
    out = tmp_path / "output.png"

    # Create dummy 100x100 image
    img = Image.new("RGB", (100, 100), color="red")
    img.save(src, "PNG")

    res = await make_image_wide(src_path=src, out_path=out)

    assert res.is_file()
    assert res.stat().st_size > 0

    with Image.open(res) as out_img:
        # Check wide dimensions: 100*1.4=140, 100*0.65=65
        assert out_img.size == (140, 65)


def test_wide_feature_loaded() -> None:
    features = load_features()
    names = [f.name for f in features]
    assert "wide" in names
