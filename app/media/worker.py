"""Supervise blocking media tools and their subprocesses as one process group."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.core.errors import ConversionError

_slots: asyncio.Semaphore | None = None


async def run_worker(kind: str, *args: Any) -> Any:
    global _slots
    settings = get_settings()
    socket_path = os.environ.get("MEDIA_WORKER_SOCKET")
    if kind != "lottie" and socket_path:
        async with asyncio.timeout(settings.job_timeout):
            reader, writer = await asyncio.open_unix_connection(socket_path, limit=8 * 1024 * 1024)
            try:
                writer.write(json.dumps([kind, args], default=str).encode() + b"\n")
                await writer.drain()
                reply = json.loads(await reader.readline())
                if "error" in reply:
                    raise ConversionError(reply["error"])
                return reply["result"]
            finally:
                writer.close()
                await writer.wait_closed()
    if _slots is None:
        _slots = asyncio.Semaphore(max(1, settings.max_concurrent_jobs))
    async with asyncio.timeout(settings.job_timeout), _slots:
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "result.json"
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "app.media.worker",
                kind,
                str(result),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                await proc.communicate(json.dumps(args, default=str).encode())
                if proc.returncode or not result.is_file():
                    raise ConversionError("پردازش رسانه متوقف شد.")
                if result.stat().st_size > 8 * 1024 * 1024:
                    raise ConversionError("حجم اطلاعات رسانه بیش از حد مجاز است.")
                payload = json.loads(await asyncio.to_thread(result.read_bytes))
                if "error" in payload:
                    raise ConversionError(payload["error"])
                return payload["result"]
            finally:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()


def main() -> None:
    import resource

    from app.media import downloader as dl
    from app.media.lottie import _render_tgs_worker

    kind, output = sys.argv[1:]
    a = json.load(sys.stdin)
    limit = 256 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    try:
        value: Any
        if kind == "probe":
            value = asdict(dl._probe_info_sync(*a))
        elif kind == "lottie":
            value = _render_tgs_worker(*a)
        elif kind == "spotify":
            a[3] = Path(a[3])
            value = dl._download_spotify_track_sync(*a)
        else:
            a[1] = Path(a[1])
            funcs: dict[str, Callable[..., Any]] = {
                "video": dl._download_video_sync,
                "audio": dl._download_audio_sync,
                "gallery": dl._download_gallery_sync,
            }
            value = funcs[kind](*a)
        payload = {"result": value}
    except Exception as exc:
        payload = {"error": str(exc)[:500]}
    Path(output).write_text(json.dumps(payload, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
