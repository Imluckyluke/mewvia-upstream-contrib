"""Lightweight health watchdog for the Mewvia deployment.

Runs inside the bot process on the existing APScheduler. Checks the things that
can silently break media delivery (worker socket, WARP tunnel, disk usage) and
DMs the admins — throttled so a prolonged outage does not spam them.

Disk is the important one: this VPS filled to 100% once and the local Bot API
server fell into a restart loop.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

from aiogram import Bot

from app.config import Settings
from app.core.logging import get_logger
from app.ui.emoji import em

log = get_logger(__name__)

_alerts: dict[str, float] = {}
_COOLDOWN_SECONDS = 3600
_WORKER_SOCKET = Path("/run/mewvia/media.sock")
JOB_ID = "health_watchdog"


async def _alert(bot: Bot, settings: Settings, key: str, text: str) -> None:
    now = time.monotonic()
    if now - _alerts.get(key, 0) < _COOLDOWN_SECONDS:
        return
    _alerts[key] = now
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception as exc:
            log.warning("watchdog_notify_failed", admin_id=admin_id, error=str(exc))


async def check_health(settings: Settings, bot: Bot) -> None:
    """One watchdog pass. Safe to call from a scheduler or manually."""
    # 1. Media worker socket — the bot cannot process any media without it.
    alive = await asyncio.to_thread(_WORKER_SOCKET.is_socket)
    if not alive:
        await _alert(
            bot,
            settings,
            "worker",
            f"{em('alert')} <b>مدیا ورکر در دسترس نیست</b>\n"
            "هیچ دانلود یا تبدیلی کار نخواهد کرد. لاگ را چک کنید: "
            "<code>docker compose logs media-worker</code>",
        )

    # 2. WARP tunnel — a TCP connect to its SOCKS port is enough to know the
    #    container is alive; a full probe belongs to the media-worker anyway.
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection("warp", 1080), timeout=10)
        writer.close()
        await writer.wait_closed()
        del reader
    except Exception as exc:
        await _alert(
            bot,
            settings,
            "warp",
            f"{em('alert')} <b>تونل WARP در دسترس نیست</b>\n"
            f"اتصال به پورت SOCKS ناموفق بود: {type(exc).__name__}\n"
            "دانلودها ممکن است شکست بخورند.",
        )

    # 3. Disk — the failure mode we already saw once.
    try:
        usage = await asyncio.to_thread(shutil.disk_usage, "/")
        percent = usage.used / usage.total * 100
        if percent >= 90:
            await _alert(
                bot,
                settings,
                "disk",
                f"{em('alert')} <b>دیسک سرور {percent:.0f}% پر است</b>\n"
                "پاکسازی خودکار دوشنبهها اجرا میشود؛ "
                "برای اجرای دستی: <code>/usr/local/bin/disk-guard.sh</code>",
            )
    except Exception as exc:
        log.warning("watchdog_disk_probe_failed", error=str(exc))
