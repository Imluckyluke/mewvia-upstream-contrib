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
from pathlib import Path

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.admin.backup import create_db_backup
from app.config import Settings
from app.core.logging import get_logger
from app.core.settings_store import get_settings_store

log = get_logger(__name__)

_JOB_ID = "autobackup"
_KEEP_DAYS = 7

_scheduler: AsyncIOScheduler | None = None
_last_cron: str | None = None


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler


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

    if not cron:
        if _scheduler is not None and _scheduler.get_job(_JOB_ID):
            _scheduler.remove_job(_JOB_ID)
            log.info("autobackup_disabled")
        _last_cron = None
        return None

    try:
        trigger = CronTrigger.from_crontab(cron)
    except ValueError as exc:
        log.warning("autobackup_bad_cron", cron=cron, error=str(exc))
        return _last_cron

    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
        _scheduler.start()
        log.info("autobackup_scheduler_started")

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
