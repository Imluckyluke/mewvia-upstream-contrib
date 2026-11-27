"""Tiny file-backed cache shared by short-lived media worker processes.

Each worker job runs in its own subprocess, so an in-memory cache would be
thrown away every time. Keys get their own file under the tmpfs volume to
avoid concurrent writers corrupting a single shared JSON document.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

_CACHE_DIR = Path("/data/tmp/.hotcache")


def _path_for(key: str) -> Path:
    return _CACHE_DIR / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.json"


def get(key: str, ttl: int) -> Any | None:
    """Return a cached value younger than ttl seconds, or None."""
    try:
        payload = json.loads(_path_for(key).read_text(encoding="utf-8"))
        if time.time() - float(payload["ts"]) < ttl:
            return payload["v"]
    except Exception:
        pass
    return None


def set(key: str, value: Any) -> None:
    """Store a value best-effort — cache failures must never break a download."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _path_for(key).write_text(json.dumps({"ts": time.time(), "v": value}), encoding="utf-8")
    except Exception:
        pass
