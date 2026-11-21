"""Liveness heartbeat.

The bot uses long polling, so there is no HTTP port to probe. Instead it
touches a file every 30s; the Docker healthcheck considers the container
unhealthy if that file goes stale.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import GetUpdates, Response, TelegramMethod
from aiogram.methods.base import TelegramType

from app.core.logging import get_logger

log = get_logger(__name__)

HEARTBEAT_INTERVAL = 30


class PollReadiness(BaseRequestMiddleware):
    """Readiness advances on successful long polls, including empty responses."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.last_write = float("-inf")

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        response = await make_request(bot, method)
        if isinstance(method, GetUpdates) and time.monotonic() - self.last_write >= 10:
            await asyncio.to_thread(self.path.touch)
            self.last_write = time.monotonic()
        return response


async def heartbeat(path: Path, interval: int = HEARTBEAT_INTERVAL) -> None:
    """Touch `path` forever. Cancelled during shutdown."""
    # Writing a few bytes to a local tmpfs is fast enough not to warrant an
    # executor hop; ASYNC240 is about avoiding blocking I/O in general.
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        while True:
            try:
                path.write_text(str(int(time.time())), encoding="utf-8")  # noqa: ASYNC240
            except OSError as exc:
                log.warning("heartbeat_write_failed", path=str(path), error=str(exc))
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)  # noqa: ASYNC240
        raise
