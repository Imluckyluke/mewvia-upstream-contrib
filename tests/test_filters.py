"""Unit tests for media visual filters."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.features import load_features
from app.media.filters import FILTER_MAP, apply_filter_to_image
from PIL import Image


def test_filters_map_has_all_filters() -> None:
    assert "bw" in FILTER_MAP
    assert "negative" in FILTER_MAP
    assert "sepia" in FILTER_MAP
    assert "bright" in FILTER_MAP
    assert "blur" in FILTER_MAP


@pytest.mark.asyncio
async def test_apply_filter_to_image(tmp_path: Path) -> None:
    src = tmp_path / "input.png"
    out = tmp_path / "output.png"

    # Create dummy 100x100 red image
    img = Image.new("RGB", (100, 100), color="red")
    img.save(src, "PNG")

    res = await apply_filter_to_image(src, out, "bw")

    assert res.is_file()
    assert res.stat().st_size > 0

    with Image.open(res) as out_img:
        assert out_img.size == (100, 100)


def test_filters_feature_loaded() -> None:
    features = load_features()
    names = [f.name for f in features]
    assert "filters" in names
