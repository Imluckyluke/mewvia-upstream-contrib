"""Automatic database backup scheduler.

The admin sets `autobackup_cron` (e.g. ``0 4 * * *``) — empty means off.
On boot (and whenever the setting changes) :func:`sync_autobackup_job`
reconciles the APScheduler job.

Each run pg_dumps the database, delivers the encrypted dump straight to every
admin as a Telegram document, reports the outcome, and then deletes the local
file: the server disk is not treated as backup storage.
"""

from __future__ import annotations

import contextlib
import datetime
import shutil
import time
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.admin.backup import create_db_backup
from app.admin.watchdog import JOB_ID as WATCHDOG_JOB_ID
from app.admin.watchdog import check_health
from app.config import Settings
from app.core.logging import get_logger
from app.core.settings_store import get_settings_store
from app.ui.emoji import em

log = get_logger(__name__)

_JOB_ID = "autobackup"
_CLEANUP_JOB_ID = "disk_cache_cleanup"
_KEEP_DAYS = 7

_scheduler: AsyncIOScheduler | None = None
_last_cron: str | None = None


async def notify_admins(bot: Bot, settings: Settings, text: str) -> None:
    """Best-effort DM to every configured admin."""
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception as exc:
            log.warning("admin_notify_failed", admin_id=admin_id, error=str(exc))


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler


def _prune_disk_cache(settings: Settings) -> None:
    """Purge stale temporary files and cached media older than 24h to protect disk space."""
    now = time.time()
    cutoff_tmp = now - (2 * 3600)  # 2 hours for temp files

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

    # Local Bot API owns its volume. Never infer deletability there from mtime.
    _prune_old_backups(settings.backup_dir)


def _prune_old_backups(backup_dir: Path, keep_days: int = _KEEP_DAYS) -> int:
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=keep_days)
    pruned = 0
    kept_bytes = 0
    kept_count = 0
    for dump in sorted(backup_dir.glob("mewvia_backup_*"), reverse=True):
        if not dump.name.endswith((".dump", ".dump.age", ".partial")):
            continue
        try:
            stat = dump.stat()
            mtime = datetime.datetime.fromtimestamp(stat.st_mtime, tz=datetime.UTC)
        except OSError:
            continue
        stale = mtime < cutoff
        if not dump.name.endswith(".partial"):
            stale |= kept_count >= 14 or kept_bytes + stat.st_size > 4 * 1024**3
            if not stale:
                kept_count += 1
                kept_bytes += stat.st_size
        if stale:
            try:
                dump.unlink()
                pruned += 1
            except OSError as exc:
                log.warning("autobackup_prune_failed", path=str(dump), error=str(exc))
    return pruned


async def _run_autobackup(settings: Settings, bot: Bot | None = None) -> None:
    """Scheduled backup: create, deliver to admins, then wipe the local copy."""
    log.info("autobackup_run_start")
    backup_file: Path | None = None
    try:
        backup_file = await create_db_backup(settings, settings.backup_dir)
        size = backup_file.stat().st_size
        pruned = _prune_old_backups(settings.backup_dir)
        log.info(
            "autobackup_run_done",
            path=str(backup_file),
            size=size,
            pruned=pruned,
        )
    except Exception as exc:
        log.error("autobackup_run_failed", error=str(exc))
        if bot is not None and settings.backup_notify:
            await notify_admins(
                bot,
                settings,
                f"❌ <b>بکاپ خودکار ناموفق بود</b>\n<code>{exc}</code>",
            )
        return

    delivered = 0
    failed = 0
    if bot is not None:
        caption = (
            f"{em('archive')} <b>بکاپ خودکار دیتابیس Mewvia</b>\n"
            f"{em('calendar')} <code>{backup_file.name}</code>\n"
            f"📦 {backup_file.stat().st_size / 1024:.0f} KB"
        )
        if backup_file.name.endswith(".age"):
            caption += f"\n{em('lock')} رمزنگاری‌شده با کلید عمومی"
        for admin_id in settings.admin_ids:
            try:
                await bot.send_document(admin_id, FSInputFile(backup_file), caption=caption)
                delivered += 1
            except Exception as exc:
                failed += 1
                log.warning("autobackup_delivery_failed", admin_id=admin_id, error=str(exc))
        log.info("autobackup_delivered", delivered=delivered, failed=failed)

    if delivered and settings.backup_deliver_to_admin:
        # The admin has the only copy that matters; do not leave user data on disk.
        with contextlib.suppress(OSError):
            backup_file.unlink(missing_ok=True)
            log.info("autobackup_local_copy_removed", path=str(backup_file))

    if bot is not None and settings.backup_notify and not delivered:
        await notify_admins(
            bot,
            settings,
            f"{em('warning')} <b>بکاپ ساخته شد ولی تحویل داده نشد</b>\n"
            f"<code>{backup_file.name}</code> روی سرور باقی ماند.",
        )


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

    # Health watchdog — worker / WARP / disk alerts, throttled hourly.
    if not _scheduler.get_job(WATCHDOG_JOB_ID):
        _scheduler.add_job(
            check_health,
            trigger=IntervalTrigger(minutes=15),
            args=[settings, bot],
            id=WATCHDOG_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        log.info("watchdog_scheduled", minutes=15)

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
        args=[settings, bot],
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
