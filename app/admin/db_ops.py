"""Database-level admin operations used by the restore flow.

Restores never write straight into the live database: the dump is applied to a
scratch database, verified, and only then swapped in. The previous live database
is renamed instead of dropped so a bad restore can be undone from the panel.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

import asyncpg

from app.config import Settings
from app.core.errors import MewviaError
from app.core.logging import get_logger

log = get_logger(__name__)

SCRATCH_PREFIX = "mewvia_restore_"
PREVIOUS_PREFIX = "mewvia_previous_"


@dataclass(frozen=True, slots=True)
class DatabaseSnapshot:
    """Row-count summary used to decide whether a restore looks sane."""

    users: int
    conversions: int
    settings: int
    emoji_slots: int
    alembic_revision: str | None

    @property
    def summary(self) -> str:
        rev = self.alembic_revision or "—"
        return (
            f"👥 کاربر: <b>{self.users:,}</b> | 🔁 تبدیل: <b>{self.conversions:,}</b> | "
            f"⚙️ تنظیمات: <b>{self.settings}</b> | 🎨 ایموجی: <b>{self.emoji_slots}</b> | "
            f"🧬 ریویژن: <code>{rev}</code>"
        )


async def _connect(settings: Settings, database: str) -> asyncpg.Connection:
    return await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=database,
        timeout=30,
    )


async def _maintenance_exec(settings: Settings, *statements: str) -> None:
    """Run DDL that needs the maintenance database (no cross-db transactions)."""
    conn = await _connect(settings, "postgres")
    try:
        for statement in statements:
            await conn.execute(statement)
    finally:
        await conn.close()


def scratch_name() -> str:
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
    return f"{SCRATCH_PREFIX}{stamp}"


async def drop_database(settings: Settings, name: str) -> None:
    """Drop a database, disconnecting leftover clients first."""
    guard = ("mewvia", "postgres")
    if name in guard:
        raise MewviaError(f"دیتابیس محافظت‌شده: {name}", code="restore_failed")
    await _maintenance_exec(
        settings,
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{name}' AND pid <> pg_backend_pid()",
        f'DROP DATABASE IF EXISTS "{name}"',
    )
    log.info("database_dropped", database=name)


async def create_empty_database(settings: Settings, name: str) -> None:
    if name not in ("mewvia", "postgres"):
        await drop_database(settings, name)
    await _maintenance_exec(settings, f'CREATE DATABASE "{name}" OWNER "{settings.postgres_user}"')
    log.info("database_created", database=name)


async def snapshot(settings: Settings, database: str) -> DatabaseSnapshot:
    """Count the tables that matter, tolerating a database being mid-restore."""
    conn = await _connect(settings, database)
    try:

        async def count(table: str) -> int:
            try:
                return int(await conn.fetchval(f'SELECT count(*) FROM "{table}"'))
            except Exception:
                return 0

        revision: str | None
        try:
            revision = await conn.fetchval("SELECT version_num FROM alembic_version")
        except Exception:
            revision = None
        return DatabaseSnapshot(
            users=await count("users"),
            conversions=await count("conversions"),
            settings=await count("settings"),
            emoji_slots=await count("emoji_slots"),
            alembic_revision=revision,
        )
    finally:
        await conn.close()


async def swap_databases(settings: Settings, source: str, target: str | None = None) -> str:
    """Make `source` the live database; the old live one is kept as previous.

    Returns the name the previous live database was parked under.
    """
    live = target or settings.postgres_db
    previous = f"{PREVIOUS_PREFIX}{datetime.datetime.now(datetime.UTC):%Y%m%d%H%M%S}"
    await _maintenance_exec(
        settings,
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname IN ('{live}', '{source}') AND pid <> pg_backend_pid()",
        f'ALTER DATABASE "{live}" RENAME TO "{previous}"',
        f'ALTER DATABASE "{source}" RENAME TO "{live}"',
    )
    log.info("database_swapped", previous=previous, promoted=source, live=live)
    return previous


async def list_previous(settings: Settings) -> list[str]:
    conn = await _connect(settings, "postgres")
    try:
        rows = await conn.fetch(
            "SELECT datname FROM pg_database WHERE datname LIKE $1 ORDER BY datname DESC",
            f"{PREVIOUS_PREFIX}%",
        )
        return [r["datname"] for r in rows]
    finally:
        await conn.close()
