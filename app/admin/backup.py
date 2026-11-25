"""Database backup helper (pg_dump -Fc + gzip compression).

Creates custom-format PostgreSQL dumps that can be restored with pg_restore.
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


async def create_db_backup(settings: Settings, out_dir: Path) -> Path:
    """Run pg_dump to produce a compressed .dump file inside out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

    if settings.backup_require_encryption and not settings.backup_age_recipient:
        raise MewviaError("Backup encryption recipient is not configured")
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = out_dir / f"mewvia_backup_{timestamp}_{uuid.uuid4().hex}.dump"
    partial = backup_path.with_suffix(".partial")

    password = settings.postgres_password

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

    env = {k: os.environ[k] for k in ("PATH", "LD_LIBRARY_PATH", "SYSTEMROOT") if k in os.environ}
    env["PGPASSWORD"] = password

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
            )
            try:
                if await asyncio.wait_for(age.wait(), 120) != 0:
                    raise MewviaError("Backup encryption failed")
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
