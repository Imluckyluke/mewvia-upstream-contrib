"""Toolchain self-check: `python -m app.doctor` (or `./dev.sh doctor`).

Verifies everything the media engine and data layer will depend on, so phase-0
completion is a single command rather than a manual checklist.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from collections.abc import Awaitable, Callable

from app import __version__
from app.config import get_settings

OK = "\033[32mOK\033[0m"
FAIL = "\033[31mFAIL\033[0m"
WARN = "\033[33mWARN\033[0m"


class CheckFailed(Exception):
    pass


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise CheckFailed(f"{' '.join(cmd)} exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    return proc.stdout or proc.stderr


MIN_PYTHON = (3, 12)


async def check_python() -> str:
    if sys.version_info[:2] < MIN_PYTHON:
        have = sys.version.split()[0]
        raise CheckFailed(f"need Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}, have {have}")
    return sys.version.split()[0]


async def check_ffmpeg() -> str:
    if not shutil.which("ffmpeg"):
        raise CheckFailed("ffmpeg not on PATH")
    first = _run(["ffmpeg", "-version"]).splitlines()[0]
    return first.replace("ffmpeg version ", "")[:60]


async def check_ffprobe() -> str:
    if not shutil.which("ffprobe"):
        raise CheckFailed("ffprobe not on PATH")
    return _run(["ffprobe", "-version"]).splitlines()[0].replace("ffprobe version ", "")[:60]


async def check_vp9_encoder() -> str:
    """Video stickers require VP9 with an alpha-capable pixel format."""
    encoders = _run(["ffmpeg", "-hide_banner", "-encoders"])
    if "libvpx-vp9" not in encoders:
        raise CheckFailed("libvpx-vp9 encoder missing — video stickers impossible")
    formats = _run(["ffmpeg", "-hide_banner", "-pix_fmts"])
    if "yuva420p" not in formats:
        raise CheckFailed("yuva420p pixel format missing — sticker transparency impossible")
    return "libvpx-vp9 + yuva420p"


async def check_webp_encoder() -> str:
    encoders = _run(["ffmpeg", "-hide_banner", "-encoders"])
    if "libwebp" not in encoders:
        raise CheckFailed("libwebp encoder missing — static stickers impossible")
    return "libwebp"


async def check_rlottie() -> str:
    """TGS rendering. The wheel bundles the native library."""
    try:
        import rlottie_python
    except Exception as exc:  # pragma: no cover - import failure path
        raise CheckFailed(f"rlottie_python import failed: {exc}") from exc
    return getattr(rlottie_python, "__version__", "installed")


async def check_pillow() -> str:
    from PIL import Image

    return f"Pillow {Image.__version__}"


async def check_pg_dump() -> str:
    """Needed by the admin backup feature (phase 5)."""
    if not shutil.which("pg_dump"):
        raise CheckFailed("pg_dump not on PATH — backups will not work")
    return _run(["pg_dump", "--version"]).strip()[:60]


async def check_tmp_dir() -> str:
    settings = get_settings()
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    probe = settings.tmp_dir / ".doctor"
    probe.write_bytes(b"ok")
    probe.unlink()
    return f"{settings.tmp_dir} writable"


async def check_postgres() -> str:
    import asyncpg

    settings = get_settings()
    conn = await asyncio.wait_for(
        asyncpg.connect(
            user=settings.postgres_user,
            password=settings.postgres_password,
            database=settings.postgres_db,
            host=settings.postgres_host,
            port=settings.postgres_port,
        ),
        timeout=10,
    )
    try:
        version = await conn.fetchval("SHOW server_version")
    finally:
        await conn.close()
    return f"PostgreSQL {version}"


async def check_redis() -> str:
    from redis.asyncio import Redis

    settings = get_settings()
    client = Redis.from_url(settings.redis_url)
    try:
        await asyncio.wait_for(client.ping(), timeout=10)
        info = await client.info("server")
    finally:
        await client.aclose()
    return f"Redis {info.get('redis_version', '?')}"


async def check_telegram() -> str:
    from app.bot import build_bot

    settings = get_settings()
    bot = build_bot(settings)
    try:
        me = await asyncio.wait_for(bot.get_me(), timeout=20)
    finally:
        await bot.session.close()
    mode = "local API" if settings.uses_local_api else "public API"
    return f"@{me.username} via {mode}"


# name -> (coroutine, required)
CHECKS: list[tuple[str, Callable[[], Awaitable[str]], bool]] = [
    ("python", check_python, True),
    ("ffmpeg", check_ffmpeg, True),
    ("ffprobe", check_ffprobe, True),
    ("vp9 + alpha", check_vp9_encoder, True),
    ("webp encoder", check_webp_encoder, True),
    ("rlottie (tgs)", check_rlottie, True),
    ("pillow", check_pillow, True),
    ("pg_dump", check_pg_dump, False),
    ("tmp dir", check_tmp_dir, True),
    ("postgres", check_postgres, True),
    ("redis", check_redis, True),
    ("telegram", check_telegram, False),
]


async def run_all() -> int:
    print(f"Mewvia doctor — v{__version__}\n")
    failures = 0
    warnings = 0
    width = max(len(name) for name, _, _ in CHECKS)

    for name, check, required in CHECKS:
        try:
            detail = await check()
        except Exception as exc:
            label = FAIL if required else WARN
            if required:
                failures += 1
            else:
                warnings += 1
            print(f"  {name:<{width}}  {label}  {exc}")
        else:
            print(f"  {name:<{width}}  {OK}    {detail}")

    print()
    if failures:
        print(f"{failures} required check(s) failed.")
        return 1
    if warnings:
        print(f"All required checks passed ({warnings} optional warning(s)).")
        return 0
    print("All checks passed.")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run_all()))


if __name__ == "__main__":
    main()
