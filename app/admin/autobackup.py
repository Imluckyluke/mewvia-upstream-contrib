"""Automatic database backup scheduler.

The admin sets `autobackup_cron` (e.g. ``0 4 * * *``) — empty means off.
On boot (and whenever the setting changes) :func:`sync_autobackup_job`
reconciles the APScheduler job. Each run pg_dumps the database into
``settings.backup_dir`` and prunes files older than 7 days so the disk
never fills up.

Delivery: the dump is saved server-side (backup_dir) because there may be
no admin chat to send it to at 4 AM. The admin panel's manual backup button
still sends the file on demand.
"""

from __future__ import annotations

import datetime
import os
import shutil
import time
from pathlib import Path

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.admin.backup import create_db_backup
from app.config import Settings
from app.core.logging import get_logger
from app.core.settings_store import get_settings_store

log = get_logger(__name__)

_JOB_ID = "autobackup"
_CLEANUP_JOB_ID = "disk_cache_cleanup"
_KEEP_DAYS = 7

_scheduler: AsyncIOScheduler | None = None
_last_cron: str | None = None


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler


def _prune_disk_cache(settings: Settings) -> None:
    """Purge stale temporary files and cached media older than 24h to protect disk space."""
    now = time.time()
    cutoff_tmp = now - (2 * 3600)  # 2 hours for temp files
    cutoff_media = now - (24 * 3600)  # 24 hours for downloaded telegram-bot-api media

    # 1. Clean /data/tmp leftovers
    if settings.tmp_dir.is_dir():
        for p in settings.tmp_dir.iterdir():
            try:
                if p.stat().st_mtime < cutoff_tmp:
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink(missing_ok=True)
            except Exception:
                pass

    # 2. Clean /var/lib/telegram-bot-api media (documents, stickers, videos, temp)
    bot_api_dir = Path("/var/lib/telegram-bot-api")
    if bot_api_dir.is_dir():
        for root, _dirs, files in os.walk(bot_api_dir):
            if "binlog" in root:
                continue
            for f in files:
                if f.endswith(".binlog"):
                    continue
                fp = Path(root) / f
                try:
                    if fp.stat().st_mtime < cutoff_media:
                        fp.unlink(missing_ok=True)
                except Exception:
                    pass


def _prune_old_backups(backup_dir: Path, keep_days: int = _KEEP_DAYS) -> int:
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=keep_days)
    pruned = 0
    for dump in sorted(backup_dir.glob("mewvia_backup_*.dump")):
        try:
            mtime = datetime.datetime.fromtimestamp(dump.stat().st_mtime, tz=datetime.UTC)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                dump.unlink()
                pruned += 1
            except OSError as exc:
                log.warning("autobackup_prune_failed", path=str(dump), error=str(exc))
    return pruned


async def _run_autobackup(settings: Settings, bot: Bot | None = None) -> None:
    _ = bot  # reserved: future DM-to-admin delivery.
    log.info("autobackup_run_start")
    try:
        backup_file = await create_db_backup(settings, settings.backup_dir)
        pruned = _prune_old_backups(settings.backup_dir)
        log.info(
            "autobackup_run_done",
            path=str(backup_file),
            size=backup_file.stat().st_size,
            pruned=pruned,
        )
    except Exception as exc:
        log.error("autobackup_run_failed", error=str(exc))


def describe_schedule(cron: str) -> str:
    """Human summary of the cron expression, or the reason it's invalid."""
    try:
        CronTrigger.from_crontab(cron)
    except ValueError as exc:
        return f"نامعتبر ({exc})"
    return cron


async def sync_autobackup_job(settings: Settings, bot: Bot) -> str | None:
    """Reconcile the scheduler with the current `autobackup_cron` value.

    Returns the active cron string, or None when the job is off.
    Safe to call repeatedly (panel saves, boot) — reschedules in place.
    """
    global _scheduler, _last_cron

    try:
        cron = (await get_settings_store().get("autobackup_cron", "")).strip()
    except Exception:
        cron = ""

    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
        _scheduler.start()
        log.info("autobackup_scheduler_started")

    # Always ensure disk cache cleanup is active (every 6 hours)
    if not _scheduler.get_job(_CLEANUP_JOB_ID):
        _scheduler.add_job(
            _prune_disk_cache,
            trigger=IntervalTrigger(hours=6),
            args=[settings],
            id=_CLEANUP_JOB_ID,
            replace_existing=True,
        )
        # Run one cleanup immediately in background thread
        import asyncio

        asyncio.get_running_loop().run_in_executor(None, _prune_disk_cache, settings)

    if not cron:
        if _scheduler.get_job(_JOB_ID):
            _scheduler.remove_job(_JOB_ID)
            log.info("autobackup_disabled")
        _last_cron = None
        return None

    try:
        trigger = CronTrigger.from_crontab(cron)
    except ValueError as exc:
        log.warning("autobackup_bad_cron", cron=cron, error=str(exc))
        return _last_cron

    if cron == _last_cron and _scheduler.get_job(_JOB_ID):
        return cron

    _scheduler.add_job(
        _run_autobackup,
        trigger=trigger,
        args=[settings],
        id=_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _last_cron = cron
    log.info("autobackup_scheduled", cron=cron)
    return cron


async def shutdown_autobackup() -> None:
    global _scheduler, _last_cron
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    _last_cron = None
