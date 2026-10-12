"""Bugfix-5 acceptance tests: slots, join URL, ffmpeg limiter/timeout, probe timeout."""

from __future__ import annotations

import asyncio
import time


def test_new_slots_seeded() -> None:
    """convert/search/user slots must exist so no button renders ❓."""
    from app.db.seed import EMOJI_SLOTS

    slot_names = {s.slot for s in EMOJI_SLOTS}
    for slot in ("convert", "search", "user"):
        assert slot in slot_names, f"emoji slot {slot!r} is used but not seeded"


def test_new_slots_have_unicode_fallbacks() -> None:
    from app.db.seed import EMOJI_SLOTS

    for declared in EMOJI_SLOTS:
        if declared.slot in ("convert", "search", "user"):
            assert declared.fallback, f"slot {declared.slot} has no fallback"


def test_ffmpeg_limiter_caps_concurrency() -> None:
    """At most N ffmpeg jobs run at once; the rest wait."""
    import asyncio as _asyncio

    from app.config import Settings
    from app.media.ffmpeg import _slots, reset_ffmpeg_limiter

    reset_ffmpeg_limiter()
    try:
        s = Settings(bot_token="111111:TEST-TOKEN-FOR-PYTEST", max_concurrent_jobs=2)
        sem = _slots(s.max_concurrent_jobs)
        assert isinstance(sem, _asyncio.Semaphore)
        # Semaphore internal value reflects the configured cap.
        assert sem._value == 2
    finally:
        reset_ffmpeg_limiter()


async def test_run_media_job_times_out() -> None:
    """A wedged job raises ConversionTimeoutError, not a hang."""
    import pytest
    from app.config import Settings
    from app.core.errors import ConversionTimeoutError
    from app.media.jobs import reset_media_limiter, run_media_job

    reset_media_limiter()
    try:
        s = Settings(bot_token="111111:TEST-TOKEN-FOR-PYTEST", job_timeout=1)

        async def wedged() -> str:
            await asyncio.sleep(30)
            return "never"

        started = time.perf_counter()
        with pytest.raises(ConversionTimeoutError):
            await run_media_job(s, wedged, stage="test_wedge")
        assert time.perf_counter() - started < 10
    finally:
        reset_media_limiter()


async def test_run_media_job_passes_through_result() -> None:
    from app.config import Settings
    from app.media.jobs import reset_media_limiter, run_media_job

    reset_media_limiter()
    try:
        s = Settings(bot_token="111111:TEST-TOKEN-FOR-PYTEST")

        async def quick() -> int:
            return 42

        assert await run_media_job(s, quick) == 42
    finally:
        reset_media_limiter()


async def test_probe_timeout_on_hung_process(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """probe() must not hang forever on a corrupt file."""
    import pytest
    from app.core.errors import ProbeError
    from app.media.probe import probe

    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(b"\x00\x01\x02not a real media file" * 100)
    # Should raise quickly (ProbeError), not hang.
    with pytest.raises(ProbeError):
        await probe(garbage, timeout=20.0)
