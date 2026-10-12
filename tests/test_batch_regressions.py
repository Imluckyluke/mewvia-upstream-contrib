"""Regression tests for bugs found in the batch-transformation refactor.

Guards against:
1. `MediaKind` missing from the audio router (NameError on speed change).
2. `render_batch_menu` signature drift (bot-based rendering).
3. Batch callback handlers returning before `query.answer` (30s spinner).
4. Missing even-dimension scale in the still-image → animation encoder
   (libx264 + yuv420p rejects odd sizes).
"""

from __future__ import annotations

import inspect
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_audio_router_exposes_media_kind() -> None:
    """set_speed compares against MediaKind.VOICE — the name must exist."""
    import importlib

    # `from app.features.audio import router` would give the Router *object*
    # (the package attribute shadows the submodule); import the module itself.
    mod = importlib.import_module("app.features.audio.router")
    assert hasattr(mod, "MediaKind"), "audio router must import MediaKind"


def test_render_batch_menu_takes_bot_not_message() -> None:
    """The menu is rendered via Bot.edit_message_text, not Message.edit_text."""
    from app.features.batch.router import render_batch_menu

    params = list(inspect.signature(render_batch_menu).parameters)
    assert params == ["bot", "chat_id", "message_id"]


def test_batch_handlers_answer_before_early_return() -> None:
    """Every early return in a batch callback handler must answer first,
    otherwise Telegram shows a loading spinner for ~30 seconds."""
    src = (ROOT / "app" / "features" / "batch" / "router.py").read_text(encoding="utf-8")
    starts = [m.start() for m in re.finditer(r"\nasync def ", src)] + [len(src)]
    problems: list[str] = []

    for i in range(len(starts) - 1):
        block = src[starts[i] : starts[i + 1]]
        header = block.split("\n", 2)[1][:60] if "\n" in block else block[:60]
        if "CallbackQuery" not in block[:400]:
            continue

        first_answer = block.find("await query.answer")
        if first_answer == -1:
            problems.append(f"{header}: never answers")
            continue

        before = block[:first_answer]
        if re.search(r"\n\s+return\s*(\n|$)", before):
            problems.append(f"{header}: return before first query.answer")

    assert not problems, "\n".join(problems)


def test_static_animation_args_force_even_dimensions() -> None:
    """libx264 + yuv420p requires even width/height; Pillow resize can produce odds."""
    from app.media.chained_pipeline import _static_animation_args

    args = _static_animation_args(pathlib.Path("in.png"), pathlib.Path("out.mp4"), 1.0)
    joined = " ".join(args)
    assert "scale=trunc(iw/2)*2:trunc(ih/2)*2" in joined
    assert "-loop" in args and "libx264" in args and "yuv420p" in args


def test_ffmpeg_pipeline_supports_png_frame_target() -> None:
    """Video → WEBP goes through a PNG frame first, so the pipeline must
    know how to emit a single still frame."""
    src = (ROOT / "app" / "media" / "chained_pipeline.py").read_text(encoding="utf-8")
    assert "target.id == TARGET_PNG.id" in src
    assert '"-vframes", "1"' in src


def test_group_media_hides_batch_for_audio() -> None:
    """Voice/audio have their own tool row; the batch button must be gated."""
    src = (ROOT / "app" / "features" / "group_media" / "router.py").read_text(encoding="utf-8")
    marker = 'BatchCB(action="menu")'
    idx = src.find(marker)
    assert idx != -1
    # The guard must appear before the batch button construction.
    guard = "kind not in (MediaKind.VOICE, MediaKind.AUDIO)"
    guard_idx = src.find(guard)
    assert guard_idx != -1, "voice/audio guard missing"
    assert guard_idx < idx
