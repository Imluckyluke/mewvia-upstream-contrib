"""Database backup and restore helpers (pg_dump / pg_restore with age encryption).

Backups are produced as custom-format PostgreSQL dumps. When an age recipient is
configured the dump is encrypted and the plaintext never leaves the temp
directory. Restoring is the admin panel's job: the dump is verified, applied to a
scratch database first, and only swapped in when it looks sane.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import sys
import uuid
from pathlib import Path

from app.config import Settings
from app.core.errors import MewviaError
from app.core.logging import get_logger

log = get_logger(__name__)

# pg_dump of a healthy Mewvia database is a few hundred kB; anything beyond this
# is either a runaway query or a hostile input and is refused before it is used.
DUMP_MAGIC = b"PGDMP"
_MAX_RESTORE_BYTES = 2 * 1024 * 1024 * 1024


def _safe_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Minimal environment for Postgres client tools — never the bot token."""
    env = {k: os.environ[k] for k in ("PATH", "LD_LIBRARY_PATH", "SYSTEMROOT") if k in os.environ}
    if extra:
        env.update(extra)
    return env


async def create_db_backup(settings: Settings, out_dir: Path) -> Path:
    """Run pg_dump to produce a compressed .dump file inside out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

    if settings.backup_require_encryption and not settings.backup_age_recipient:
        raise MewviaError("Backup encryption recipient is not configured")
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = out_dir / f"mewvia_backup_{timestamp}_{uuid.uuid4().hex}.dump"
    partial = backup_path.with_suffix(".partial")

    cmd = [
        sys.executable,
        "-c",
        "import os,resource,sys; n=int(sys.argv[1]); "
        "resource.setrlimit(resource.RLIMIT_FSIZE,(n,n)); "
        "os.umask(0o077); os.execvp(sys.argv[2],sys.argv[2:])",
        str(settings.backup_max_bytes),
        "pg_dump",
        "-h",
        settings.postgres_host,
        "-p",
        str(settings.postgres_port),
        "-U",
        settings.postgres_user,
        "-d",
        settings.postgres_db,
        "-Fc",  # custom compressed format
        "-f",
        str(partial),
    ]

    env = _safe_env({"PGPASSWORD": settings.postgres_password})

    log.info("backup_start", destination=str(backup_path))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.communicate()
            if proc.returncode != 0:
                partial.unlink(missing_ok=True)
    except Exception as exc:
        log.error("backup_failed_to_spawn", error=str(exc))
        raise MewviaError(f"failed to spawn pg_dump: {exc}", code="backup_failed") from exc

    if proc.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="replace").strip()
        log.error("backup_pg_dump_error", returncode=proc.returncode, stderr=err_msg)
        raise MewviaError(f"pg_dump failed: {err_msg}", code="backup_failed")

    if settings.backup_age_recipient:
        encrypted = backup_path.with_suffix(".age.partial")
        try:
            age = await asyncio.create_subprocess_exec(
                "age",
                "-r",
                settings.backup_age_recipient,
                "-o",
                str(encrypted),
                str(partial),
                stdin=asyncio.subprocess.DEVNULL,
                env=_safe_env(),
            )
            try:
                if await asyncio.wait_for(age.wait(), 120) != 0:
                    raise MewviaError("Backup encryption failed", code="backup_failed")
            finally:
                if age.returncode is None:
                    age.kill()
                    await age.wait()
            backup_path = backup_path.with_suffix(".dump.age")
            encrypted.replace(backup_path)
        finally:
            partial.unlink(missing_ok=True)
            encrypted.unlink(missing_ok=True)
    else:
        partial.replace(backup_path)
    log.info("backup_complete", path=str(backup_path), size=backup_path.stat().st_size)
    return backup_path


def _identity_file(settings: Settings, work_dir: Path) -> Path | None:
    """Materialise BACKUP_AGE_IDENTITY so `age -d` can read it without a tty."""
    secret = settings.backup_age_identity.strip()
    if not secret:
        return None
    key_path = work_dir / "backup-identity.key"
    key_path.write_text(secret + "\n", encoding="utf-8")
    key_path.chmod(0o600)
    return key_path


async def decrypt_backup(settings: Settings, encrypted: Path, work_dir: Path) -> Path:
    """Decrypt an .age dump into work_dir. The identity never touches argv."""
    if not settings.backup_age_identity:
        raise MewviaError(
            "برای بازکردن بکاپ رمزشده، BACKUP_AGE_IDENTITY روی سرور تنظیم نشده است.",
            code="restore_no_identity",
        )
    identity = _identity_file(settings, work_dir)
    if identity is None:
        raise MewviaError("Backup identity is not configured", code="restore_no_identity")
    plain = work_dir / "restore.dump"
    proc = await asyncio.create_subprocess_exec(
        "age",
        "-d",
        "-i",
        str(identity),
        "-o",
        str(plain),
        str(encrypted),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=_safe_env(),
    )
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=300)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.communicate()
    if proc.returncode != 0:
        detail = err.decode("utf-8", errors="replace").strip()[:300]
        log.error("backup_decrypt_failed", error=detail)
        raise MewviaError(
            "رمزگشایی بکاپ ناموفق بود؛ کلید با فایل هم‌خوانی ندارد.",
            code="restore_failed",
        )
    return plain


def inspect_dump(dump_path: Path) -> tuple[bool, str]:
    """Cheap sanity gate before a restore is allowed to touch the server."""
    if not dump_path.is_file():
        return False, "فایل بکاپ یافت نشد."
    size = dump_path.stat().st_size
    if size == 0:
        return False, "فایل بکاپ خالی است."
    if size > _MAX_RESTORE_BYTES:
        return False, "حجم فایل بکاپ بیش از حد مجاز است."
    head = dump_path.read_bytes()[:5]
    if head != DUMP_MAGIC:
        return False, "این فایل یک دامپ معتبر PostgreSQL (فرمت custom) نیست."
    return True, ""


async def restore_into_database(settings: Settings, dump_path: Path, target_db: str) -> None:
    """pg_restore a verified dump into an existing, empty database."""
    ok, reason = inspect_dump(dump_path)
    if not ok:
        raise MewviaError(reason, code="restore_failed")

    cmd = [
        "pg_restore",
        "-h",
        settings.postgres_host,
        "-p",
        str(settings.postgres_port),
        "-U",
        settings.postgres_user,
        "-d",
        target_db,
        "--no-owner",
        "--no-privileges",
        "--single-transaction",
        str(dump_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=_safe_env({"PGPASSWORD": settings.postgres_password}),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=1800)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.communicate()
    if proc.returncode != 0:
        detail = err.decode("utf-8", errors="replace").strip()[:500]
        log.error("restore_failed", target=target_db, error=detail)
        raise MewviaError(f"بازگردانی دیتابیس ناموفق بود: {detail}", code="restore_failed")
    log.info("restore_done", target=target_db, source=str(dump_path))
