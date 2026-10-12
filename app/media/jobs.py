"""Bounded media-job execution: concurrency cap + timeout.

Every feature that shells out to ffmpeg (or downloads a large file first)
must go through :func:`run_media_job`. Two guarantees:

* **concurrency cap** — at most ``settings.max_concurrent_jobs`` heavy jobs
  run at once; the rest wait on an ``asyncio.Semaphore`` instead of OOMing
  the container. Before this, the "/health" panel *claimed* 4 parallel jobs
  but nothing enforced it.
* **timeout** — ``settings.job_timeout`` bounds the whole job (download +
  process), so one corrupt file can never wedge a handler forever.

The semaphore is per-process (module-level, lazily sized from the first
settings seen). Tests reset it via :func:`reset_media_limiter` so sizes
don't leak between cases.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.config import Settings
from app.core.errors import ConversionTimeoutError
from app.core.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")

_limiter: asyncio.Semaphore | None = None
_limiter_size: int = 0


def reset_media_limiter() -> None:
    """Drop the cached semaphore (test isolation)."""
    global _limiter, _limiter_size
    _limiter = None
    _limiter_size = 0


def _get_limiter(settings: Settings) -> asyncio.Semaphore:
    global _limiter, _limiter_size
    size = max(1, settings.max_concurrent_jobs)
    if _limiter is None or _limiter_size != size:
        _limiter = asyncio.Semaphore(size)
        _limiter_size = size
        log.info("media_limiter_configured", max_concurrent_jobs=size)
    return _limiter


async def run_media_job(
    settings: Settings,
    job: Callable[[], Awaitable[T]],
    *,
    stage: str = "media_job",
) -> T:
    """Run ``job`` under the concurrency cap and the global job timeout."""
    limiter = _get_limiter(settings)
    timeout = max(1, settings.job_timeout)
    async with limiter:
        try:
            return await asyncio.wait_for(job(), timeout=timeout)
        except TimeoutError:
            log.warning("media_job_timeout", stage=stage, timeout=timeout)
            raise ConversionTimeoutError(f"{stage} timed out after {timeout}s") from None
