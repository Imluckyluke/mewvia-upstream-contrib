"""Database backup helper (pg_dump -Fc + gzip compression).

Creates custom-format PostgreSQL dumps that can be restored with pg_restore.
"""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path

from app.config import Settings
from app.core.errors import MewviaError
from app.core.logging import get_logger

log = get_logger(__name__)


async def create_db_backup(settings: Settings, out_dir: Path) -> Path:
    """Run pg_dump to produce a compressed .dump file inside out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = out_dir / f"mewvia_backup_{timestamp}.dump"

    password = settings.postgres_password

    cmd = [
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
        str(backup_path),
    ]

    env = {"PGPASSWORD": password}

    log.info("backup_start", destination=str(backup_path))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()
    except Exception as exc:
        log.error("backup_failed_to_spawn", error=str(exc))
        raise MewviaError(f"failed to spawn pg_dump: {exc}", code="backup_failed") from exc

    if proc.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="replace").strip()
        log.error("backup_pg_dump_error", returncode=proc.returncode, stderr=err_msg)
        raise MewviaError(f"pg_dump failed: {err_msg}", code="backup_failed")

    log.info("backup_complete", path=str(backup_path), size=backup_path.stat().st_size)
    return backup_path
