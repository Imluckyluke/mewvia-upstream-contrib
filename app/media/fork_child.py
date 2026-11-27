"""Helpers for running a media job inside a forked child process.

A fresh ``python -m app.media.worker`` costs ~4.7s just to import yt-dlp,
mutagen and Pillow on every single job. Forking the already-warmed socket
server keeps the same isolation guarantees — separate PID, separate RLIMIT,
separate exit status — without paying the import cost again.
"""

from __future__ import annotations

import json
import os
import resource
import sys
from pathlib import Path
from typing import Any

from app.core.errors import ConversionError


def _dispatch(kind: str, args: list[Any]) -> Any:
    from app.media import downloader as dl
    from app.media.lottie import _render_tgs_worker

    if kind == "probe":
        return asdict_safe(dl._probe_info_sync(*args))
    if kind == "lottie":
        return _render_tgs_worker(*args)
    if kind == "spotify":
        args[3] = Path(args[3])
        return dl._download_spotify_track_sync(*args)
    if kind in ("video", "audio", "gallery"):
        args[1] = Path(args[1])
        dispatch: Any = {
            "video": dl._download_video_sync,
            "audio": dl._download_audio_sync,
            "gallery": dl._download_gallery_sync,
        }
        return dispatch[kind](*args)
    raise ConversionError(f"unknown worker kind: {kind}")


def asdict_safe(value: Any) -> Any:
    from dataclasses import asdict, is_dataclass

    return asdict(value) if is_dataclass(value) and not isinstance(value, type) else value


def child_main(kind: str, args: list[Any], result_path: Path, file_limit: int) -> None:
    """Run one job in this (already forked) process and write its result.

    Always ends with ``os._exit``: flushing inherited asyncio state in a
    forked child would be incorrect and can hang.
    """
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
        value = _dispatch(kind, args)
        result_path.write_text(json.dumps({"result": value}, default=str), encoding="utf-8")
    except BaseException as exc:
        try:
            result_path.write_text(json.dumps({"error": str(exc)[:500]}), encoding="utf-8")
        except Exception:
            pass
    finally:
        sys.stdout.flush()
        os._exit(0)
